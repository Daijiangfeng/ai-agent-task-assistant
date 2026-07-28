"""
Agent 执行相关 API 路由。
提供 Agent 任务的启动执行功能。
"""


from fastapi import APIRouter, BackgroundTasks, Depends

from app.api.deps import get_agent_service, get_task_service
from app.api.errors import TaskNotFoundException, TaskStateException
from app.models.api_schemas import TaskResponse
from app.models.task import TaskStatus
from app.services.agent_service import AgentService
from app.services.task_service import TaskService

router = APIRouter(prefix="/tasks", tags=["agent"])


@router.post("/{task_id}/execute", response_model=TaskResponse)
async def execute_task(
    task_id: str,
    background_tasks: BackgroundTasks,
    task_service: TaskService = Depends(get_task_service),
    agent_service: AgentService = Depends(get_agent_service),
):
    """
    启动 Agent Workflow 执行任务（异步）。

    任务将在后台执行，可通过 GET /tasks/{task_id} 查询进度。
    """
    task = await task_service.get_task(task_id)
    if task is None:
        raise TaskNotFoundException(task_id)

    if task.status not in (TaskStatus.PENDING, TaskStatus.FAILED):
        raise TaskStateException(
            task_id=task_id,
            current_status=task.status.value,
            allowed_statuses=[TaskStatus.PENDING.value, TaskStatus.FAILED.value],
        )

    # 在后台异步执行 Agent Workflow
    background_tasks.add_task(
        agent_service.run_task,
        task_id=task_id,
        goal=task.goal,
        context=task.context,
    )

    # 立即返回，状态更新为 PLANNING
    await task_service.update_task_status(task_id, TaskStatus.PLANNING)
    task = await task_service.get_task(task_id)
    return await task_service.to_task_response(task)
