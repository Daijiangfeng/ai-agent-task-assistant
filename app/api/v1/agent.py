"""
Agent 执行相关 API 路由。
提供 Agent 任务的启动执行功能。
"""


from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException

from app.api.deps import get_agent_service, get_task_service
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
        raise HTTPException(status_code=404, detail=f"任务 {task_id} 不存在")

    if task.status not in (TaskStatus.PENDING, TaskStatus.FAILED):
        raise HTTPException(
            status_code=400,
            detail=f"任务当前状态为 {task.status.value}，无法执行。"
            f"仅 PENDING 或 FAILED 状态的任务可以执行。",
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
