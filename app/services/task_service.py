"""
任务管理服务。
负责任务的 CRUD 操作和状态管理。
当前使用内存存储，后续可接入 PostgreSQL。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from app.config.logging import get_logger
from app.models.api_schemas import TaskResponse, TaskStatusResponse
from app.models.task import Task, TaskStatus

logger = get_logger(__name__)


class TaskService:
    """
    任务生命周期管理服务。

    当前使用内存字典存储任务，
    后续可替换为 PostgreSQL + Redis 持久化实现。
    """

    def __init__(self):
        self._tasks: dict[str, Task] = {}

    async def create_task(self, goal: str, context: str | None = None) -> str:
        """
        创建新任务。

        Args:
            goal: 用户目标描述。
            context: 可选的上下文信息。

        Returns:
            任务 ID (UUID)。
        """
        task_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        task = Task(
            id=task_id,
            goal=goal,
            context=context,
            status=TaskStatus.PENDING,
            created_at=now,
            updated_at=now,
        )

        self._tasks[task_id] = task
        logger.info("TaskService: 任务创建成功", task_id=task_id, goal=goal)
        return task_id

    async def get_task(self, task_id: str) -> Task | None:
        """
        获取任务详情。

        Args:
            task_id: 任务 ID。

        Returns:
            Task 实例，不存在返回 None。
        """
        return self._tasks.get(task_id)

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
        task = self._tasks.get(task_id)
        if not task:
            return None

        task.status = status
        task.updated_at = datetime.now(timezone.utc).isoformat()

        for key, value in kwargs.items():
            if hasattr(task, key):
                setattr(task, key, value)

        logger.info(
            "TaskService: 任务状态更新",
            task_id=task_id,
            status=status.value,
        )
        return task

    async def list_tasks(self, limit: int = 20, offset: int = 0) -> list[Task]:
        """
        列表查询任务。

        Args:
            limit: 返回数量限制。
            offset: 偏移量。

        Returns:
            Task 列表。
        """
        tasks = sorted(
            self._tasks.values(),
            key=lambda t: t.created_at,
            reverse=True,
        )
        return tasks[offset : offset + limit]

    async def get_task_count(self) -> int:
        """获取任务总数。"""
        return len(self._tasks)

    async def get_task_status_response(self, task_id: str) -> TaskStatusResponse | None:
        """
        获取任务状态响应（用于 API 返回）。

        Args:
            task_id: 任务 ID。

        Returns:
            TaskStatusResponse 实例，不存在返回 None。
        """
        task = self._tasks.get(task_id)
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
            plan=None,  # 可扩展返回详细计划
            final_result=task.final_result,
        )

    async def to_task_response(self, task: Task) -> TaskResponse:
        """将 Task 转换为 API 响应格式。"""
        return TaskResponse(
            task_id=task.id,
            status=task.status,
            plan=None,
            created_at=task.created_at,
        )
