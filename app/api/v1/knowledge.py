"""
知识库相关 API 路由。
提供文档入库和语义检索功能。
"""

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_rag_service
from app.config.logging import get_logger
from app.models.api_schemas import (
    IngestDocumentRequest,
    IngestDocumentResponse,
    KnowledgeSearchRequest,
    KnowledgeSearchResponse,
    KnowledgeSearchResult,
)
from app.rag.service import RAGService

logger = get_logger(__name__)

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


@router.post("/documents", response_model=IngestDocumentResponse, status_code=201)
async def ingest_document(
    request: IngestDocumentRequest,
    rag_service: RAGService = Depends(get_rag_service),
):
    """
    将本地文件加载、分块、向量化并索引到知识库。

    - **file_path**: 待索引文件路径（支持 PDF/DOCX/TXT/Markdown）
    """
    try:
        result = await rag_service.ingest_file(request.file_path)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("文档入库失败", error=str(e), file_path=request.file_path)
        raise HTTPException(status_code=500, detail=f"文档入库失败: {str(e)}")

    return IngestDocumentResponse(
        source=result["source"],
        chunks_indexed=result["chunks_indexed"],
    )


@router.post("/search", response_model=KnowledgeSearchResponse)
async def search_knowledge(
    request: KnowledgeSearchRequest,
    rag_service: RAGService = Depends(get_rag_service),
):
    """
    在知识库中做语义检索，返回最相关的文档片段。

    - **query**: 检索查询文本
    - **top_k**: 可选，返回数量（默认使用配置）
    """
    try:
        results = await rag_service.search(request.query, top_k=request.top_k)
    except Exception as e:
        logger.error("知识库检索失败", error=str(e), query=request.query)
        raise HTTPException(status_code=500, detail=f"检索失败: {str(e)}")

    return KnowledgeSearchResponse(
        query=request.query,
        results=[
            KnowledgeSearchResult(
                content=item.get("content", ""),
                metadata=item.get("metadata", {}),
                score=item.get("score"),
            )
            for item in results
        ],
    )
