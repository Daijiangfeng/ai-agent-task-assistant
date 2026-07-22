"""
RAG 服务。
组合 loader / splitter / indexer / retriever，提供文档入库和检索的高层接口。
"""

from __future__ import annotations

from typing import Any

from app.config.logging import get_logger
from app.config.settings import Settings, get_settings
from app.llm.embeddings import BaseEmbeddingProvider
from app.llm.factory import create_embedding_provider
from app.rag.indexer import ChromaIndexer
from app.rag.retriever import ChromaRetriever
from app.rag.splitter import TextSplitter
from app.rag.vector_store import ChromaStore

logger = get_logger(__name__)


class RAGService:
    """
    RAG 高层服务。

    对外提供：
    - ingest_file(path): 加载并索引单个文件
    - search(query, top_k): 语义检索相关片段
    """

    def __init__(
        self,
        settings: Settings | None = None,
        embedding_provider: BaseEmbeddingProvider | None = None,
        vector_store: ChromaStore | None = None,
    ):
        self._settings = settings or get_settings()
        self._embedding = embedding_provider or create_embedding_provider(
            self._settings
        )
        self._store = vector_store or ChromaStore(self._settings.chroma_dir)
        splitter = TextSplitter(self._settings)
        self._indexer = ChromaIndexer(self._embedding, self._store, splitter)
        self._retriever = ChromaRetriever(self._embedding, self._store)

    async def ingest_file(self, file_path: str) -> dict[str, Any]:
        """
        加载并索引单个文件。

        Args:
            file_path: 文件路径。

        Returns:
            包含 source 和 chunk 数量的结果字典。
        """
        chunk_ids = await self._indexer.index_file(file_path)
        return {
            "source": file_path,
            "chunks_indexed": len(chunk_ids),
            "chunk_ids": chunk_ids,
        }

    async def search(self, query: str, top_k: int | None = None) -> list[dict[str, Any]]:
        """
        语义检索相关文档片段。

        Args:
            query: 查询文本。
            top_k: 返回数量，默认使用配置 RAG_TOP_K。

        Returns:
            结果列表，每项含 content、metadata、score。
        """
        k = top_k or self._settings.RAG_TOP_K
        documents = await self._retriever.retrieve(query, top_k=k)
        return [
            {
                "content": doc.content,
                "metadata": {k: v for k, v in doc.metadata.items() if k != "score"},
                "score": doc.metadata.get("score"),
            }
            for doc in documents
        ]
