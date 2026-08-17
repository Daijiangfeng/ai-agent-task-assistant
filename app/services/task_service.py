"""
任务管理服务。
负责任务的 CRUD 操作和状态管理。

存储后端可插拔（见 app.services.task_repository）：
- auto（默认）：优先 PostgreSQL（SQLAlchemy Async），不可用降级内存；
- postgres / sqlite / memory：强制指定（TASK_STORAGE_BACKEND 配置）。

生产环境配置 PostgreSQL 后，任务持久化存储，服务重启不丢失，
支持审计与历史查询；多租户按 tenant_id 隔离。
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

from app.config.logging import get_logger
from app.config.settings import Settings, get_settings
from app.models.api_schemas import TaskResponse, TaskStatusResponse
from app.models.plan import Plan, ReflectionResult
from app.models.task import SubTask, Task, TaskStatus
from app.services.task_repository import (
    MEMORY_BACKEND,
    POSTGRES_BACKEND,
    SQLITE_BACKEND,
    InMemoryTaskRepository,
    SQLAlchemyTaskRepository,
    TaskRepository,
)

logger = get_logger(__name__)


def _utcnow_iso() -> str:
    """返回当前 UTC 时间的 ISO 8601 字符串。"""
    return datetime.now(timezone.utc).isoformat()


class TaskService:
    """
    任务生命周期管理服务。

    存储后端：
    - auto：启动后首次访问时探测 PostgreSQL，可用则持久化，否则降级内存；
    - postgres / sqlite：SQLAlchemy Async 持久化；
    - memory：进程内字典。
    """

    def __init__(
        self,
        settings: Settings | None = None,
        backend: str | None = None,
    ):
        self._settings = settings or get_settings()
        self._backend = (backend or self._settings.TASK_STORAGE_BACKEND).lower()
        self._repo: TaskRepository | None = None
        self._repo_lock = asyncio.Lock()

        if self._backend in (POSTGRES_BACKEND, SQLITE_BACKEND):
            self._repo = SQLAlchemyTaskRepository(self._settings, self._backend)
        elif self._backend == MEMORY_BACKEND:
            self._repo = InMemoryTaskRepository()
        elif self._backend != "auto":
            logger.warning(
                "未知任务存储后端 %r，降级为内存存储", self._backend
            )
            self._repo = InMemoryTaskRepository()

    async def _get_repo(self) -> TaskRepository:
        """懒加载仓库；auto 模式探测 PostgreSQL，失败降级内存。"""
        if self._repo is not None:
            return self._repo
        async with self._repo_lock:
            if self._repo is not None:
                return self._repo
            if self._backend == "auto":
                candidate = SQLAlchemyTaskRepository(self._settings, POSTGRES_BACKEND)
                if await candidate.probe():
                    logger.info("TaskService: 使用 PostgreSQL 持久化存储")
                    self._repo = candidate
                else:
                    logger.warning("TaskService: PostgreSQL 不可用，降级为内存存储")
                    self._repo = InMemoryTaskRepository()
            else:
                self._repo = InMemoryTaskRepository()
            return self._repo

    async def create_task(
        self,
        goal: str,
        context: str | None = None,
        owner_id: str = "anonymous",
        tenant_id: str = "default",
    ) -> str:
        """
        创建新任务。

        Args:
            goal: 用户目标描述。
            context: 可选的上下文信息。
            owner_id: 任务所有者（创建者）用户 ID。
            tenant_id: 任务所属租户 ID（多租户隔离）。

        Returns:
            任务 ID (UUID)。
        """
        repo = await self._get_repo()
        task_id = str(uuid.uuid4())
        now = _utcnow_iso()

        task = Task(
            id=task_id,
            goal=goal,
            context=context,
            owner_id=owner_id,
            tenant_id=tenant_id,
            status=TaskStatus.PENDING,
            created_at=now,
            updated_at=now,
        )

        await repo.create(task)
        logger.info(
            "TaskService: 任务创建成功",
            task_id=task_id,
            goal=goal,
            tenant_id=tenant_id,
        )
        return task_id

    async def get_task(self, task_id: str) -> Task | None:
        """
        获取任务详情。

        Args:
            task_id: 任务 ID。

        Returns:
            Task 实例，不存在返回 None。
        """
        repo = await self._get_repo()
        return await repo.get(task_id)

    async def update_task_status(
        self,
        task_id: str,
        status: TaskStatus,
        **kwargs,
    ) -> Task | None:
        """
        更新任务状态。

        Args:
            task_id: 任务 ID。
            status: 新状态。
            **kwargs: 其他需要更新的字段（如 final_result, plan 等）。

        Returns:
            更新后的 Task 实例，不存在返回 None。
        """
        repo = await self._get_repo()
        task = await repo.get(task_id)
        if not task:
            return None

        task.status = status
        task.updated_at = _utcnow_iso()

        for key, value in kwargs.items():
            if hasattr(task, key):
                setattr(task, key, value)

        await repo.update(task)
        logger.info(
            "TaskService: 任务状态更新",
            task_id=task_id,
            status=status.value,
        )
        return task

    async def list_tasks(
        self,
        limit: int = 20,
        offset: int = 0,
        owner_id: str | None = None,
        tenant_id: str | None = None,
    ) -> list[Task]:
        """
        列表查询任务。

        Args:
            limit: 返回数量限制。
            offset: 偏移量。
            owner_id: 仅返回该所有者的任务；None 表示全部（admin 视角）。
            tenant_id: 仅返回该租户的任务；None 表示全部（admin 视角）。

        Returns:
            Task 列表。
        """
        repo = await self._get_repo()
        return await repo.list(
            limit=limit,
            offset=offset,
            owner_id=owner_id,
            tenant_id=tenant_id,
        )

    async def get_task_count(
        self,
        owner_id: str | None = None,
        tenant_id: str | None = None,
    ) -> int:
        """
        获取任务总数。

        Args:
            owner_id: 可选，仅统计该所有者的任务。
            tenant_id: 可选，仅统计该租户的任务。
        """
        repo = await self._get_repo()
        return await repo.count(owner_id=owner_id, tenant_id=tenant_id)

    async def count_by_status(self) -> dict[str, int]:
        """按状态统计任务数量（供仪表盘使用）。"""
        repo = await self._get_repo()
        return await repo.count_by_status()

    async def sync_plan(self, task_id: str, plan: dict) -> None:
        """
        将 Planner/Replanner 产出的计划写回任务。

        从 plan dict 重建 Plan / SubTask 模型，同步 task.plan、task.subtasks、plan_version。
        子任务初始状态为 PENDING。
        """
        repo = await self._get_repo()
        task = await repo.get(task_id)
        if not task or not plan:
            return
        raw_subtasks = plan.get("subtasks", []) or []
        subtasks: list[SubTask] = []
        for i, st in enumerate(raw_subtasks):
            subtasks.append(
                SubTask(
                    id=str(st.get("id", f"task_{i}")),
                    description=str(st.get("description", "")),
                    status=TaskStatus.PENDING,
                    dependencies=[str(d) for d in st.get("dependencies", [])],
                )
            )
        version = int(plan.get("version", task.plan_version) or task.plan_version)
        task.plan = Plan(
            goal=str(plan.get("goal", task.goal)),
            subtasks=subtasks,
            version=version,
            reasoning=plan.get("reasoning"),
        )
        task.subtasks = subtasks
        task.plan_version = version
        task.updated_at = _utcnow_iso()
        await repo.update(task)

    async def sync_task_results(
        self, task_id: str, task_results: list[dict]
    ) -> None:
        """
        根据 Executor 累加的 task_results 更新对应子任务的状态/结果/所用工具/错误。

        task_results 每项结构：{subtask_id, description, result, status, error?}，
        status 为 "completed" / "failed" 字符串。
        """
        repo = await self._get_repo()
        task = await repo.get(task_id)
        if not task or not task_results:
            return
        by_id = {s.id: s for s in task.subtasks}
        for i, r in enumerate(task_results):
            sid = str(r.get("subtask_id", f"task_{i}"))
            subtask = by_id.get(sid)
            if subtask is None:
                continue
            raw_status = str(r.get("status", "")).lower()
            if raw_status == "completed":
                subtask.status = TaskStatus.COMPLETED
            elif raw_status == "failed":
                subtask.status = TaskStatus.FAILED
            subtask.result = r.get("result")
            subtask.tool_used = r.get("tool_used")
            subtask.error = r.get("error")
        task.updated_at = _utcnow_iso()
        await repo.update(task)

    async def sync_reflection(
        self,
        task_id: str,
        reflection: dict | None,
        iteration_count: int | None = None,
    ) -> None:
        """将反思评估结果与迭代次数写回任务。"""
        repo = await self._get_repo()
        task = await repo.get(task_id)
        if not task:
            return
        if reflection:
            try:
                task.reflection = ReflectionResult(**reflection)
            except Exception:  # pragma: no cover - 防御性，字段缺失时忽略
                task.reflection = None
        if iteration_count is not None:
            task.iteration_count = iteration_count
        task.updated_at = _utcnow_iso()
        await repo.update(task)

    async def get_task_status_response(self, task_id: str) -> TaskStatusResponse | None:
        """
        获取任务状态响应（用于 API 返回）。

        Args:
            task_id: 任务 ID。

        Returns:
            TaskStatusResponse 实例，不存在返回 None。
        """
        repo = await self._get_repo()
        task = await repo.get(task_id)
        if not task:
            return None

        # 计算进度
        total_subtasks = len(task.subtasks)
        completed = sum(
            1 for s in task.subtasks if s.status == TaskStatus.COMPLETED
        )
        progress = (completed / total_subtasks * 100) if total_subtasks > 0 else 0.0

        current_step = None
        if task.status == TaskStatus.EXECUTING and total_subtasks > 0:
            for i, s in enumerate(task.subtasks):
                if s.status == TaskStatus.PENDING:
                    current_step = f"执行子任务 {i + 1}/{total_subtasks}: {s.description}"
                    break

        return TaskStatusResponse(
            task_id=task.id,
            status=task.status,
            current_step=current_step,
            progress=progress,
            plan=task.plan,
            subtasks=task.subtasks,
            reflection=task.reflection,
            iteration_count=task.iteration_count,
            plan_version=task.plan_version,
            error=task.error,
            final_result=task.final_result,
        )

    async def to_task_response(self, task: Task) -> TaskResponse:
        """将 Task 转换为 API 响应格式。"""
        return TaskResponse(
            task_id=task.id,
            status=task.status,
            plan=task.plan,
            created_at=task.created_at,
        )
