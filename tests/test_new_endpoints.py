"""
新增只读接口（stats / tools）与任务状态回写逻辑测试。
覆盖 B2（Agent 状态回写）与 B3（stats/tools/knowledge 只读接口）。
"""

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_rag_service
from app.config.settings import Settings
from app.services.task_service import TaskService
from main import app


class _StubRAGService:
    """离线桩 RAG 服务。

    stats 端点仅需 list_documents / count_chunks（只读向量库计数），
    真实 RAGService 在构造时会急切初始化智谱 Embedding（需 ANTHROPIC_AUTH_TOKEN），
    在无 .env 的环境（如 CI）会失败。用桩覆盖依赖使该端点测试离线可跑。
    """

    async def list_documents(self) -> list:
        return []

    async def count_chunks(self) -> int:
        return 0


@pytest.fixture
def client():
    """创建测试客户端。

    覆盖 get_rag_service 为离线桩，避免依赖 .env 中的 ANTHROPIC_AUTH_TOKEN，
    与 AGENTS.md「测试全部离线可跑」保持一致。
    """
    app.dependency_overrides[get_rag_service] = lambda: _StubRAGService()
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_rag_service, None)


class TestStatsAndToolsAPI:
    """B3：系统概览统计与工具清单接口。"""

    def test_get_stats(self, client: TestClient):
        """/stats 返回任务分布、工具数与知识库计数。"""
        response = client.get("/api/v1/stats")
        assert response.status_code == 200
        data = response.json()
        assert "version" in data
        assert "task_total" in data
        assert isinstance(data["tasks_by_status"], dict)
        # 内置工具已在应用启动时注册，工具数应 >= 0（不依赖具体数量）。
        assert data["tool_count"] >= 0
        assert data["knowledge_document_count"] >= 0
        assert data["knowledge_chunk_count"] >= 0

    def test_list_tools(self, client: TestClient):
        """/tools 返回已注册工具（名称 + 描述）。"""
        response = client.get("/api/v1/tools")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == len(data["tools"])
        for tool in data["tools"]:
            assert "name" in tool
            assert "description" in tool

    def test_stats_reflects_created_task(self, client: TestClient):
        """新建任务后 /stats 的总数应递增。"""
        before = client.get("/api/v1/stats").json()["task_total"]
        client.post("/api/v1/tasks/", json={"goal": "stats 统计测试"})
        after = client.get("/api/v1/stats").json()["task_total"]
        assert after == before + 1


class TestTaskStateWriteback:
    """B2：TaskService 状态回写方法（进度/plan/subtasks/reflection）。"""

    @pytest.mark.asyncio
    async def test_sync_plan_populates_subtasks(self):
        """sync_plan 应将 plan dict 重建为 Plan 与 SubTask 写回任务。"""
        service = TaskService(Settings(TASK_STORAGE_BACKEND="memory"))
        task_id = await service.create_task(goal="写回测试")
        await service.sync_plan(
            task_id,
            {
                "goal": "写回测试",
                "version": 2,
                "subtasks": [
                    {"id": "task_0", "description": "第一步"},
                    {"id": "task_1", "description": "第二步", "dependencies": ["task_0"]},
                ],
            },
        )
        task = await service.get_task(task_id)
        assert task.plan is not None
        assert len(task.subtasks) == 2
        assert task.plan_version == 2
        assert task.subtasks[1].dependencies == ["task_0"]

    @pytest.mark.asyncio
    async def test_sync_task_results_updates_progress(self):
        """sync_task_results 完成子任务后，进度应正确计算（不再恒为 0）。"""
        from app.models.task import TaskStatus

        service = TaskService(Settings(TASK_STORAGE_BACKEND="memory"))
        task_id = await service.create_task(goal="进度测试")
        await service.sync_plan(
            task_id,
            {
                "subtasks": [
                    {"id": "task_0", "description": "A"},
                    {"id": "task_1", "description": "B"},
                ]
            },
        )
        await service.update_task_status(task_id, TaskStatus.EXECUTING)
        await service.sync_task_results(
            task_id,
            [
                {
                    "subtask_id": "task_0",
                    "description": "A",
                    "result": "done",
                    "status": "completed",
                }
            ],
        )
        resp = await service.get_task_status_response(task_id)
        assert resp.progress == 50.0
        assert resp.subtasks[0].status == TaskStatus.COMPLETED
        assert resp.subtasks[0].result == "done"

    @pytest.mark.asyncio
    async def test_count_by_status(self):
        """count_by_status 返回覆盖全部状态枚举的计数字典。"""
        from app.models.task import TaskStatus

        service = TaskService(Settings(TASK_STORAGE_BACKEND="memory"))
        await service.create_task(goal="a")
        await service.create_task(goal="b")
        counts = await service.count_by_status()
        assert counts[TaskStatus.PENDING.value] == 2
        # 所有状态键均应存在。
        for status in TaskStatus:
            assert status.value in counts
