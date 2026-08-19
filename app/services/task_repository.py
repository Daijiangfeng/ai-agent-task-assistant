"""
任务持久化仓库层。

提供统一 TaskRepository 接口与三种后端：
- InMemoryTaskRepository：进程内字典，开发/测试/降级用；
- SQLAlchemyTaskRepository：SQLAlchemy Async（PostgreSQL asyncpg / SQLite aiosqlite），
  生产持久化，服务重启后任务不丢失，支持审计与历史查询。

后端选择策略（TaskService 内部）：
- auto：优先 PostgreSQL，不可用降级内存；
- postgres / sqlite / memory：强制指定。
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config.logging import get_logger
from app.config.settings import Settings
from app.models import Plan, ReflectionResult, SubTask, Task, TaskStatus
from app.models.task import ApprovalRequest
from app.models.task_record import Base, TaskRecord

logger = get_logger(__name__)


def _coerce_text(value: Any) -> str:
    """将历史脏数据中的 LLM content block 列表规范化为纯文本。

    修复前（未做 content 归一化）写入的 subtask/agent result 可能是
    Anthropic 风格 block 列表（如 [{"type": "text", "text": "..."}] 或
    [{"type": "tool_use", "id": "...", "name": "...", "input": {...}}]），
    Pydantic 校验会因 result 不是字符串而报 500。读取时统一归一化。
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for block in value:
            if isinstance(block, dict):
                text = block.get("text")
                if text is not None:
                    parts.append(str(text))
                elif block.get("name"):
                    parts.append(
                        f"[tool_use {block['name']}] {block.get('input', '')}"
                    )
            else:
                parts.append(str(block))
        return "\n".join(p for p in parts if p)
    return str(value)

POSTGRES_BACKEND = "postgres"
SQLITE_BACKEND = "sqlite"
MEMORY_BACKEND = "memory"


class TaskRepository(ABC):
    """任务仓库统一接口。"""

    @abstractmethod
    async def create(self, task: Task) -> None:
        """创建任务记录。"""
        ...

    @abstractmethod
    async def get(self, task_id: str) -> Task | None:
        """按 ID 读取任务，不存在返回 None。"""
        ...

    @abstractmethod
    async def update(self, task: Task) -> None:
        """全量更新任务记录（不存在则插入）。"""
        ...

    @abstractmethod
    async def list(
        self,
        limit: int = 20,
        offset: int = 0,
        owner_id: str | None = None,
        tenant_id: str | None = None,
        status: TaskStatus | None = None,
    ) -> list[Task]:
        """分页列出任务，可按 owner_id / tenant_id / status 过滤（None 表示不限）。"""
        ...

    @abstractmethod
    async def count(
        self,
        owner_id: str | None = None,
        tenant_id: str | None = None,
    ) -> int:
        """统计任务数，可按 owner_id / tenant_id 过滤。"""
        ...

    @abstractmethod
    async def count_by_status(self) -> dict[str, int]:
        """按状态统计任务数量。"""
        ...


class InMemoryTaskRepository(TaskRepository):
    """进程内内存仓库（开发/测试/降级方案，进程重启数据丢失）。"""

    def __init__(self) -> None:
        self._tasks: dict[str, Task] = {}

    async def create(self, task: Task) -> None:
        self._tasks[task.id] = task

    async def get(self, task_id: str) -> Task | None:
        return self._tasks.get(task_id)

    async def update(self, task: Task) -> None:
        self._tasks[task.id] = task

    async def list(
        self,
        limit: int = 20,
        offset: int = 0,
        owner_id: str | None = None,
        tenant_id: str | None = None,
        status: TaskStatus | None = None,
    ) -> list[Task]:
        tasks = sorted(
            self._tasks.values(),
            key=lambda t: t.created_at,
            reverse=True,
        )
        if owner_id is not None:
            tasks = [t for t in tasks if t.owner_id == owner_id]
        if tenant_id is not None:
            tasks = [t for t in tasks if t.tenant_id == tenant_id]
        if status is not None:
            tasks = [t for t in tasks if t.status == status]
        return tasks[offset : offset + limit]

    async def count(
        self,
        owner_id: str | None = None,
        tenant_id: str | None = None,
    ) -> int:
        return len(
            await self.list(limit=10**6, owner_id=owner_id, tenant_id=tenant_id)
        )

    async def count_by_status(self) -> dict[str, int]:
        counts = {status.value: 0 for status in TaskStatus}
        for task in self._tasks.values():
            counts[task.status.value] = counts.get(task.status.value, 0) + 1
        return counts


class SQLAlchemyTaskRepository(TaskRepository):
    """
    SQLAlchemy Async 仓库（PostgreSQL / SQLite）。

    表结构见 app.models.task_record.TaskRecord，首次操作时自动建表。
    """

    def __init__(self, settings: Settings, backend: str = POSTGRES_BACKEND):
        if backend not in (POSTGRES_BACKEND, SQLITE_BACKEND):
            raise ValueError(f"不支持的 SQLAlchemy 后端: {backend!r}")
        self._backend = backend
        if backend == SQLITE_BACKEND:
            path = Path(settings.task_db_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            self._dsn = f"sqlite+aiosqlite:///{path}"
        else:
            self._dsn = settings.postgres_async_dsn
        self._engine = create_async_engine(
            self._dsn,
            echo=settings.DEBUG,
            pool_size=10,
            max_overflow=20,
            connect_args={"timeout": settings.DB_CONNECT_TIMEOUT}
            if backend == POSTGRES_BACKEND
            else {},
        )
        self._session_factory = async_sessionmaker(
            bind=self._engine, expire_on_commit=False
        )
        self._init_lock = asyncio.Lock()
        self._initialized = False
        logger.info(
            "SQLAlchemyTaskRepository 初始化", backend=backend, dsn=self._dsn
        )

    async def probe(self) -> bool:
        """探测数据库连通性（auto 后端选择用）。"""
        try:
            async with self._engine.connect() as conn:
                await conn.execute(select(1))
            return True
        except Exception as exc:
            logger.warning(
                "任务数据库探测失败",
                backend=self._backend,
                error=str(exc),
            )
            return False

    async def _ensure_schema(self) -> None:
        """首次使用时自动建表。"""
        if self._initialized:
            return
        async with self._init_lock:
            if self._initialized:
                return
            async with self._engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            self._initialized = True

    # ---- 序列化 ----

    @staticmethod
    def _to_record_data(task: Task) -> dict:
        return {
            "id": task.id,
            "goal": task.goal,
            "context": task.context,
            "owner_id": task.owner_id,
            "tenant_id": task.tenant_id,
            "status": task.status.value,
            "plan": task.plan.model_dump(mode="json") if task.plan else None,
            "subtasks": [s.model_dump(mode="json") for s in task.subtasks] or None,
            "reflection": (
                task.reflection.model_dump(mode="json")
                if task.reflection
                else None
            ),
            "plan_version": task.plan_version,
            "iteration_count": task.iteration_count,
            "execution_mode": task.execution_mode,
            "agent_results": task.agent_results or None,
            "pending_approval": (
                task.pending_approval.model_dump(mode="json")
                if task.pending_approval
                else None
            ),
            "approval_history": (
                [a.model_dump(mode="json") for a in task.approval_history] or None
            ),
            "final_result": task.final_result,
            "error": task.error,
            "created_at": task.created_at,
            "updated_at": task.updated_at,
        }

    @staticmethod
    def _to_task(record: TaskRecord) -> Task:
        return Task(
            id=record.id,
            goal=record.goal,
            context=record.context,
            owner_id=record.owner_id,
            tenant_id=record.tenant_id,
            status=TaskStatus(record.status),
            plan=Plan.model_validate(record.plan) if record.plan else None,
            subtasks=[
                SubTask.model_validate(
                    {**s, "result": _coerce_text(s.get("result"))}
                )
                for s in (record.subtasks or [])
            ],
            reflection=(
                ReflectionResult.model_validate(record.reflection)
                if record.reflection
                else None
            ),
            plan_version=record.plan_version,
            iteration_count=record.iteration_count,
            execution_mode=record.execution_mode,
            agent_results=[
                {**r, "result": _coerce_text(r.get("result"))}
                for r in (record.agent_results or [])
            ],
            pending_approval=(
                ApprovalRequest.model_validate(record.pending_approval)
                if record.pending_approval
                else None
            ),
            approval_history=[
                ApprovalRequest.model_validate(a)
                for a in (record.approval_history or [])
            ],
            final_result=record.final_result,
            error=record.error,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )

    # ---- TaskRepository 实现 ----

    async def create(self, task: Task) -> None:
        await self._ensure_schema()
        async with self._session_factory() as session:
            session.add(TaskRecord(**self._to_record_data(task)))
            await session.commit()

    async def get(self, task_id: str) -> Task | None:
        await self._ensure_schema()
        async with self._session_factory() as session:
            record = await session.get(TaskRecord, task_id)
            if record is None:
                return None
            return self._to_task(record)

    async def update(self, task: Task) -> None:
        await self._ensure_schema()
        data = self._to_record_data(task)
        async with self._session_factory() as session:
            record = await session.get(TaskRecord, task.id)
            if record is None:
                session.add(TaskRecord(**data))
            else:
                for key, value in data.items():
                    setattr(record, key, value)
            await session.commit()

    async def list(
        self,
        limit: int = 20,
        offset: int = 0,
        owner_id: str | None = None,
        tenant_id: str | None = None,
        status: TaskStatus | None = None,
    ) -> list[Task]:
        await self._ensure_schema()
        stmt = select(TaskRecord).order_by(TaskRecord.created_at.desc())
        if owner_id is not None:
            stmt = stmt.where(TaskRecord.owner_id == owner_id)
        if tenant_id is not None:
            stmt = stmt.where(TaskRecord.tenant_id == tenant_id)
        if status is not None:
            stmt = stmt.where(TaskRecord.status == status.value)
        stmt = stmt.limit(limit).offset(offset)
        async with self._session_factory() as session:
            records = (await session.scalars(stmt)).all()
            return [self._to_task(r) for r in records]

    async def count(
        self,
        owner_id: str | None = None,
        tenant_id: str | None = None,
    ) -> int:
        await self._ensure_schema()
        stmt = select(func.count()).select_from(TaskRecord)
        if owner_id is not None:
            stmt = stmt.where(TaskRecord.owner_id == owner_id)
        if tenant_id is not None:
            stmt = stmt.where(TaskRecord.tenant_id == tenant_id)
        async with self._session_factory() as session:
            return int((await session.execute(stmt)).scalar_one())

    async def count_by_status(self) -> dict[str, int]:
        await self._ensure_schema()
        stmt = (
            select(TaskRecord.status, func.count())
            .group_by(TaskRecord.status)
        )
        async with self._session_factory() as session:
            rows = (await session.execute(stmt)).all()
        counts = {status.value: 0 for status in TaskStatus}
        for status_value, n in rows:
            counts[str(status_value)] = int(n)
        return counts


def create_task_repository(
    settings: Settings,
    backend: str | None = None,
) -> TaskRepository:
    """
    按配置创建任务仓库（显式后端，不含 auto 探测逻辑）。

    Args:
        settings: 配置对象。
        backend: postgres | sqlite | memory；None 时使用 settings.TASK_STORAGE_BACKEND。

    Returns:
        TaskRepository 实例。
    """
    backend = (backend or settings.TASK_STORAGE_BACKEND or MEMORY_BACKEND).lower()
    if backend in (POSTGRES_BACKEND, SQLITE_BACKEND):
        return SQLAlchemyTaskRepository(settings, backend)
    return InMemoryTaskRepository()
