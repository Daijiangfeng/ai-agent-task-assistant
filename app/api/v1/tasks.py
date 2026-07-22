"""
任务相关 API 路由。
提供任务的创建、查询、列表功能。
"""

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import get_task_service
from app.models.api_schemas import (
    CreateTaskRequest,
    TaskListResponse,
    TaskResponse,
    TaskStatusResponse,
)
from app.services.task_service import TaskService

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.post("/", response_model=TaskResponse, status_code=201)
async def create_task(
    request: CreateTaskRequest,
    task_service: TaskService = Depends(get_task_service),
):
    """
    创建新的 Agent 任务。

    - **goal**: 用户目标描述
    - **context**: 可选的上下文信息
    """
    task_id = await task_service.create_task(
        goal=request.goal,
        context=request.context,
    )
    task = await task_service.get_task(task_id)
    return await task_service.to_task_response(task)


@router.get("/", response_model=TaskListResponse)
async def list_tasks(
    limit: int = Query(default=20, ge=1, le=100, description="返回数量限制"),
    offset: int = Query(default=0, ge=0, description="偏移量"),
    task_service: TaskService = Depends(get_task_service),
):
    """列表查询任务。"""
    tasks = await task_service.list_tasks(limit=limit, offset=offset)
    total = await task_service.get_task_count()

    task_responses = [
        await task_service.to_task_response(t) for t in tasks
    ]

    return TaskListResponse(total=total, tasks=task_responses)


@router.get("/{task_id}", response_model=TaskStatusResponse)
async def get_task_status(
    task_id: str,
    task_service: TaskService = Depends(get_task_service),
):
    """
    查询任务执行状态和进度。

    返回任务当前状态、进度百分比、当前步骤等信息。
    """
    response = await task_service.get_task_status_response(task_id)
    if response is None:
        raise HTTPException(status_code=404, detail=f"任务 {task_id} 不存在")
    return response
