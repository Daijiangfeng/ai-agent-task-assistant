"""
系统概览统计 API 路由。
聚合任务与工具计数，供仪表盘一次性获取概览数据。
"""

from fastapi import APIRouter, Depends

from app.api.auth import get_current_user
from app.api.deps import get_task_service
from app.config.logging import get_logger
from app.config.settings import get_settings
from app.models.api_schemas import StatsResponse
from app.services.task_service import TaskService
from app.tools.registry import ToolRegistry
from app.tools.security import ToolContext

logger = get_logger(__name__)

router = APIRouter(prefix="/stats", tags=["stats"])


@router.get("", response_model=StatsResponse)
async def get_stats(
    task_service: TaskService = Depends(get_task_service),
    user: ToolContext = Depends(get_current_user),
):
    """返回系统概览统计：任务分布与工具数。"""
    settings = get_settings()
    task_total = await task_service.get_task_count()
    tasks_by_status = await task_service.count_by_status()
    tool_count = len(ToolRegistry.get_all())

    return StatsResponse(
        version=settings.APP_VERSION,
        task_total=task_total,
        tasks_by_status=tasks_by_status,
        tool_count=tool_count,
    )
