"""
Agent 执行相关 API 路由。
提供 Agent 任务的启动执行功能（异步队列化：API 入队即返回，Worker 消费执行）。
"""


from fastapi import APIRouter, Depends

from app.api.auth import can_access_task, get_current_user
from app.api.deps import get_task_queue, get_task_service
from app.api.errors import (
    QueueUnavailableException,
    TaskForbiddenException,
    TaskNotFoundException,
    TaskStateException,
)
from app.config.logging import get_logger
from app.models.api_schemas import TaskResponse
from app.models.task import RESTARTABLE_STATUSES, TaskStatus
from app.queue.base import QueueFullError, TaskMessage, TaskQueue
from app.services.task_control import get_task_control
from app.services.task_service import TaskService
from app.tools.security import ToolContext

logger = get_logger(__name__)

router = APIRouter(prefix="/tasks", tags=["agent"])


@router.post("/{task_id}/execute", response_model=TaskResponse)
async def execute_task(
    task_id: str,
    task_service: TaskService = Depends(get_task_service),
    task_queue: TaskQueue = Depends(get_task_queue),
    user: ToolContext = Depends(get_current_user),
):
    """
    启动 Agent Workflow 执行任务（异步队列化）。

    任务消息入队后立即返回；由 Worker（独立 python -m app.worker 进程，
    或 TASK_QUEUE_EMBEDDED_WORKER=true 时的应用内嵌 Worker）消费执行。
    可通过 GET /tasks/{task_id} 查询进度。
    工具调用将携带调用者身份（ToolContext）执行权限矩阵校验。

    允许状态：
    - pending：首次执行；
    - failed / cancelled / paused / completed：重新执行
      （基于 LangGraph Checkpoint 断点续跑或从头重跑）。
    """
    task = await task_service.get_task(task_id)
    if task is None:
        raise TaskNotFoundException(task_id)

    if not can_access_task(task, user):
        raise TaskForbiddenException(task_id)

    if task.status not in RESTARTABLE_STATUSES:
        raise TaskStateException(
            task_id=task_id,
            current_status=task.status.value,
            allowed_statuses=sorted(v.value for v in RESTARTABLE_STATUSES),
        )

    # 重新执行时清除历史暂停/取消控制请求，避免 Worker 立即再次暂停/取消
    get_task_control().clear(task_id)

    # 入队（携带调用者身份），Worker 消费后执行
    try:
        await task_queue.enqueue(
            TaskMessage(
                task_id=task_id,
                goal=task.goal,
                context=task.context,
                tool_context=user.to_dict(),
            )
        )
    except QueueFullError as e:
        logger.warning("任务入队失败：队列已满", task_id=task_id)
        raise QueueUnavailableException(str(e)) from e

    # 立即返回，状态更新为 PLANNING（Worker 接手后正式进入规划）
    await task_service.update_task_status(task_id, TaskStatus.PLANNING)
    task = await task_service.get_task(task_id)
    return await task_service.to_task_response(task)
