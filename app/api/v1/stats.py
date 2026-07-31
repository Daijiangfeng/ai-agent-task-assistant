"""
系统概览统计 API 路由。
聚合任务、工具、知识库计数，供仪表盘一次性获取概览数据。
"""

from fastapi import APIRouter, Depends

from app.api.deps import get_rag_service, get_task_service
from app.config.logging import get_logger
from app.config.settings import get_settings
from app.models.api_schemas import StatsResponse
from app.rag.service import RAGService
from app.services.task_service import TaskService
from app.tools.registry import ToolRegistry

logger = get_logger(__name__)

router = APIRouter(prefix="/stats", tags=["stats"])


@router.get("", response_model=StatsResponse)
async def get_stats(
    task_service: TaskService = Depends(get_task_service),
    rag_service: RAGService = Depends(get_rag_service),
):
    """返回系统概览统计：任务分布、工具数、知识库规模。"""
    settings = get_settings()
    task_total = await task_service.get_task_count()
    tasks_by_status = await task_service.count_by_status()
    tool_count = len(ToolRegistry.get_all())

    # 知识库统计依赖向量库，出错时降级为 0，避免整个仪表盘接口失败。
    try:
        documents = await rag_service.list_documents()
        document_count = len(documents)
        chunk_count = await rag_service.count_chunks()
    except Exception as e:  # pragma: no cover - 依赖向量库
        logger.warning("知识库统计获取失败", error=str(e))
        document_count = 0
        chunk_count = 0

    return StatsResponse(
        version=settings.APP_VERSION,
        task_total=task_total,
        tasks_by_status=tasks_by_status,
        tool_count=tool_count,
        knowledge_document_count=document_count,
        knowledge_chunk_count=chunk_count,
    )
