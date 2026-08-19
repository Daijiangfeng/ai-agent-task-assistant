"""
任务存储后端测试。

覆盖：
- sqlite 持久化：任务在"服务重启"（新 TaskService 实例指向同一库文件）后不丢失；
- 列表/计数按 owner_id / tenant_id 过滤；
- 后端选择：memory / sqlite 显式指定，auto 在 PostgreSQL 不可用时降级内存。
"""

import pytest

from app.config.settings import Settings
from app.models.task import TaskStatus
from app.services.task_repository import (
    InMemoryTaskRepository,
    SQLAlchemyTaskRepository,
)
from app.services.task_service import TaskService


def _sqlite_settings(tmp_path) -> Settings:
    return Settings(
        TASK_STORAGE_BACKEND="sqlite",
        TASK_DB_PATH=str(tmp_path / "tasks.db"),
        POSTGRES_HOST="127.0.0.1",
        POSTGRES_PORT=1,
    )


class TestSqlitePersistence:
    """sqlite 后端：持久化与过滤。"""

    @pytest.mark.asyncio
    async def test_task_survives_service_restart(self, tmp_path):
        settings = _sqlite_settings(tmp_path)
        service = TaskService(settings)
        task_id = await service.create_task(
            goal="持久化测试", owner_id="u1", tenant_id="t1"
        )
        await service.update_task_status(task_id, TaskStatus.EXECUTING)
        await service.sync_plan(
            task_id,
            {
                "goal": "持久化测试",
                "subtasks": [{"id": "s1", "description": "第一步"}],
            },
        )
        await service.sync_task_results(
            task_id,
            [
                {
                    "subtask_id": "s1",
                    "description": "第一步",
                    "status": "completed",
                    "result": "完成",
                }
            ],
        )

        # 模拟服务重启：新实例指向同一 sqlite 文件
        restarted = TaskService(settings)
        task = await restarted.get_task(task_id)
        assert task is not None
        assert task.goal == "持久化测试"
        assert task.status == TaskStatus.EXECUTING
        assert task.owner_id == "u1"
        assert task.tenant_id == "t1"
        assert task.plan is not None
        assert task.subtasks[0].status == TaskStatus.COMPLETED
        assert task.subtasks[0].result == "完成"

    @pytest.mark.asyncio
    async def test_list_count_filters(self, tmp_path):
        service = TaskService(_sqlite_settings(tmp_path))
        await service.create_task(goal="g1", owner_id="alice", tenant_id="t1")
        await service.create_task(goal="g2", owner_id="alice", tenant_id="t2")
        await service.create_task(goal="g3", owner_id="bob", tenant_id="t1")

        assert len(await service.list_tasks(owner_id="alice", tenant_id="t1")) == 1
        assert len(await service.list_tasks(owner_id="alice")) == 2
        assert len(await service.list_tasks(tenant_id="t1")) == 2
        assert await service.get_task_count() == 3
        assert await service.get_task_count(owner_id="alice") == 2
        assert await service.get_task_count(tenant_id="t2") == 1

    @pytest.mark.asyncio
    async def test_count_by_status(self, tmp_path):
        service = TaskService(_sqlite_settings(tmp_path))
        await service.create_task(goal="a")
        await service.create_task(goal="b")
        counts = await service.count_by_status()
        assert counts[TaskStatus.PENDING.value] == 2

    @pytest.mark.asyncio
    async def test_status_response_progress(self, tmp_path):
        service = TaskService(_sqlite_settings(tmp_path))
        task_id = await service.create_task(goal="进度")
        await service.sync_plan(
            task_id,
            {
                "subtasks": [
                    {"id": "s0", "description": "A"},
                    {"id": "s1", "description": "B"},
                ]
            },
        )
        await service.update_task_status(task_id, TaskStatus.EXECUTING)
        await service.sync_task_results(
            task_id,
            [{"subtask_id": "s0", "description": "A", "status": "completed"}],
        )
        resp = await service.get_task_status_response(task_id)
        assert resp is not None
        assert resp.progress == 50.0
        assert resp.subtasks[0].status == TaskStatus.COMPLETED


class TestBackendSelection:
    """后端选择逻辑。"""

    def test_memory_backend(self):
        svc = TaskService(Settings(TASK_STORAGE_BACKEND="memory"))
        assert isinstance(svc._repo, InMemoryTaskRepository)

    def test_sqlite_backend(self, tmp_path):
        svc = TaskService(_sqlite_settings(tmp_path))
        assert isinstance(svc._repo, SQLAlchemyTaskRepository)

    def test_unknown_backend_falls_back_to_memory(self):
        svc = TaskService(Settings(TASK_STORAGE_BACKEND="weird"))
        assert isinstance(svc._repo, InMemoryTaskRepository)

    @pytest.mark.asyncio
    async def test_auto_falls_back_to_memory_when_postgres_unavailable(self):
        settings = Settings(
            TASK_STORAGE_BACKEND="auto",
            POSTGRES_HOST="127.0.0.1",
            POSTGRES_PORT=1,
            DB_CONNECT_TIMEOUT=1,
        )
        svc = TaskService(settings)
        task_id = await svc.create_task(goal="auto 降级测试")
        assert isinstance(svc._repo, InMemoryTaskRepository)
        task = await svc.get_task(task_id)
        assert task is not None
