"""
RAG 服务。
组合 loader / splitter / indexer / retriever，提供文档入库和检索的高层接口。
"""

from __future__ import annotations

import time
from typing import Any

from app.config.logging import get_logger
from app.config.settings import Settings, get_settings
from app.llm.embeddings import BaseEmbeddingProvider
from app.llm.factory import create_embedding_provider
from app.rag.base import BaseReranker, Document
from app.rag.indexer import RAG_COLLECTION, ChromaIndexer
from app.rag.reranker import ZhipuReranker
from app.rag.retriever import ChromaRetriever
from app.rag.splitter import TextSplitter
from app.rag.vector_store import ChromaStore

logger = get_logger(__name__)


class RAGService:
    """
    RAG 高层服务。

    对外提供：
    - ingest_file(path): 加载并索引单个文件
    - search(query, top_k): 语义检索相关片段（可选 rerank 精排）
    """

    def __init__(
        self,
        settings: Settings | None = None,
        embedding_provider: BaseEmbeddingProvider | None = None,
        vector_store: ChromaStore | None = None,
        reranker: BaseReranker | None = None,
    ):
        self._settings = settings or get_settings()
        self._embedding = embedding_provider or create_embedding_provider(
            self._settings
        )
        self._store = vector_store or ChromaStore(self._settings.chroma_dir)
        splitter = TextSplitter(self._settings)
        self._indexer = ChromaIndexer(self._embedding, self._store, splitter)
        self._retriever = ChromaRetriever(self._embedding, self._store)
        # 可插拔精排层：未注入且开关开启时默认使用智谱 Rerank
        if reranker is not None:
            self._reranker = reranker
        elif self._settings.ENABLE_RERANK:
            self._reranker = ZhipuReranker(self._settings)
        else:
            self._reranker = None

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

        ENABLE_RERANK 开启时：先召回 RETRIEVAL_TOP_K 候选，再经智谱 Rerank
        精排与阈值过滤后取前 top_k；rerank 失败时自动回退向量序结果。

        Args:
            query: 查询文本。
            top_k: 返回数量；rerank 开启时默认 RERANK_TOP_K，否则默认 RAG_TOP_K。

        Returns:
            结果列表，每项含 content、metadata、score（rerank 命中时额外含 rerank_score）。
        """
        if self._reranker is not None and self._settings.ENABLE_RERANK:
            documents = await self._search_with_rerank(query, top_k)
        else:
            k = top_k or self._settings.RAG_TOP_K
            documents = await self._retriever.retrieve(query, top_k=k)
        return [self._to_result(doc) for doc in documents]

    async def _search_with_rerank(
        self, query: str, top_k: int | None
    ) -> list[Document]:
        """
        召回 + Rerank 精排流程（失败时降级回退向量序）。

        流程：向量召回 RETRIEVAL_TOP_K 候选 -> rerank -> 按
        RERANK_SCORE_THRESHOLD 过滤 -> 取前 top_k（默认 RERANK_TOP_K）。
        """
        k = top_k or self._settings.RERANK_TOP_K
        candidates = await self._retriever.retrieve(
            query, top_k=self._settings.RETRIEVAL_TOP_K
        )
        if not candidates:
            return []

        started = time.perf_counter()
        try:
            reranked = await self._reranker.rerank(query, candidates, top_n=k)
        except Exception as e:
            # 可用性优先：rerank 失败不应拖垮整个检索链路，回退向量序结果
            logger.warning(
                "Rerank 失败，回退向量检索结果", error=str(e), query=query
            )
            return candidates[:k]

        threshold = self._settings.RERANK_SCORE_THRESHOLD
        kept = [
            doc
            for doc in reranked
            if (doc.metadata.get("rerank_score") or 0.0) >= threshold
        ]
        logger.info(
            "Rerank 精排完成",
            query=query,
            candidate_count=len(candidates),
            kept_count=len(kept),
            threshold=threshold,
            elapsed_ms=round((time.perf_counter() - started) * 1000, 1),
        )
        return kept[:k]

    @staticmethod
    def _to_result(doc: Document) -> dict[str, Any]:
        """将 Document 转为对外结果项（score/rerank_score 从 metadata 提升到顶层）。"""
        metadata = {
            k: v
            for k, v in doc.metadata.items()
            if k not in ("score", "rerank_score")
        }
        item: dict[str, Any] = {
            "content": doc.content,
            "metadata": metadata,
            "score": doc.metadata.get("score"),
        }
        if "rerank_score" in doc.metadata:
            item["rerank_score"] = doc.metadata.get("rerank_score")
        return item

    async def list_documents(self) -> list[dict[str, Any]]:
        """
        列出已索引的文档（按 source 聚合，统计每个来源的 chunk 数）。

        Returns:
            文档列表，每项含 source、type、chunk_count。
        """
        records = self._store.list_records(RAG_COLLECTION)
        grouped: dict[str, dict[str, Any]] = {}
        for rec in records:
            meta = rec.get("metadata") or {}
            source = str(meta.get("source", "unknown"))
            entry = grouped.setdefault(
                source,
                {"source": source, "type": meta.get("type"), "chunk_count": 0},
            )
            entry["chunk_count"] += 1
        return sorted(grouped.values(), key=lambda d: d["source"])

    async def count_chunks(self) -> int:
        """返回知识库中的 chunk 总数。"""
        return self._store.count(RAG_COLLECTION)

    async def delete_document(self, source: str) -> int:
        """
        删除指定来源文档的所有 chunk。

        Returns:
            被删除的 chunk 数量。
        """
        return self._store.delete_by_source(RAG_COLLECTION, source)
