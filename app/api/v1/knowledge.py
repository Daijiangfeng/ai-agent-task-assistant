"""
知识库相关 API 路由。
提供文档入库和语义检索功能。
"""

import shutil
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app.api.auth import get_current_user, require_non_guest
from app.api.deps import get_rag_service
from app.config.logging import get_logger
from app.models.api_schemas import (
    DeleteDocumentResponse,
    DocumentInfo,
    DocumentListResponse,
    IngestDocumentRequest,
    IngestDocumentResponse,
    KnowledgeSearchRequest,
    KnowledgeSearchResponse,
    KnowledgeSearchResult,
)
from app.rag.service import RAGService
from app.tools.security import ToolContext

logger = get_logger(__name__)

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


@router.post("/documents", response_model=IngestDocumentResponse, status_code=201)
async def ingest_document(
    request: IngestDocumentRequest,
    rag_service: RAGService = Depends(get_rag_service),
    user: ToolContext = Depends(require_non_guest),
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


@router.post("/upload", response_model=IngestDocumentResponse, status_code=201)
async def upload_document(
    file: UploadFile = File(...),
    rag_service: RAGService = Depends(get_rag_service),
    user: ToolContext = Depends(require_non_guest),
):
    """
    上传本地文件并索引到知识库。

    解决浏览器无法提供服务端路径的问题：将上传文件写入临时目录后复用 ingest_file。
    """
    filename = file.filename or "upload"
    suffix = Path(filename).suffix
    tmp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            delete=False, suffix=suffix
        ) as tmp:
            shutil.copyfileobj(file.file, tmp)
            tmp_path = tmp.name
        result = await rag_service.ingest_file(tmp_path)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("文档上传入库失败", error=str(e), filename=filename)
        raise HTTPException(status_code=500, detail=f"文档上传入库失败: {str(e)}")
    finally:
        await file.close()
        if tmp_path:
            Path(tmp_path).unlink(missing_ok=True)

    # source 使用原始文件名，而非临时路径，便于前端展示。
    return IngestDocumentResponse(
        source=filename,
        chunks_indexed=result["chunks_indexed"],
    )


@router.get("/documents", response_model=DocumentListResponse)
async def list_documents(
    rag_service: RAGService = Depends(get_rag_service),
    user: ToolContext = Depends(get_current_user),
):
    """列出知识库中已索引的文档（按来源聚合，含分块数）。"""
    try:
        docs = await rag_service.list_documents()
    except Exception as e:
        logger.error("文档列表获取失败", error=str(e))
        raise HTTPException(status_code=500, detail=f"文档列表获取失败: {str(e)}")
    documents = [
        DocumentInfo(
            source=d["source"],
            type=d.get("type"),
            chunk_count=d["chunk_count"],
        )
        for d in docs
    ]
    return DocumentListResponse(total=len(documents), documents=documents)


@router.delete("/documents", response_model=DeleteDocumentResponse)
async def delete_document(
    source: str,
    rag_service: RAGService = Depends(get_rag_service),
    user: ToolContext = Depends(require_non_guest),
):
    """按来源删除知识库中的一个文档（其所有分块）。

    source 作为查询参数传入，避免路径中的斜杠与路由冲突。
    """
    try:
        deleted = await rag_service.delete_document(source)
    except Exception as e:
        logger.error("文档删除失败", error=str(e), source=source)
        raise HTTPException(status_code=500, detail=f"文档删除失败: {str(e)}")
    if deleted == 0:
        raise HTTPException(status_code=404, detail=f"文档不存在: {source}")
    return DeleteDocumentResponse(source=source, chunks_deleted=deleted)


@router.post("/search", response_model=KnowledgeSearchResponse)
async def search_knowledge(
    request: KnowledgeSearchRequest,
    rag_service: RAGService = Depends(get_rag_service),
    user: ToolContext = Depends(get_current_user),
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
                rerank_score=item.get("rerank_score"),
            )
            for item in results
        ],
    )
