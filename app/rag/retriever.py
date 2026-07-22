"""
Chroma 检索器。
将查询向量化后从 Chroma 向量库检索最相关的文档片段。
"""

from __future__ import annotations

from app.config.logging import get_logger
from app.llm.embeddings import BaseEmbeddingProvider
from app.rag.base import BaseRetriever, Document
from app.rag.indexer import RAG_COLLECTION
from app.rag.vector_store import ChromaStore

logger = get_logger(__name__)


class ChromaRetriever(BaseRetriever):
    """
    Chroma 检索器。

    流程：query -> embed(智谱) -> Chroma 相似度检索 -> Document 列表。
    """

    def __init__(
        self,
        embedding_provider: BaseEmbeddingProvider,
        vector_store: ChromaStore,
        collection_name: str = RAG_COLLECTION,
    ):
        self._embedding = embedding_provider
        self._store = vector_store
        self._collection = collection_name

    async def retrieve(self, query: str, top_k: int = 5) -> list[Document]:
        """
        检索相关文档片段。

        Args:
            query: 查询文本。
            top_k: 返回最相关的 top_k 个片段。

        Returns:
            Document 列表，metadata 中含 score。
        """
        query_embedding = self._embedding.embed_query(query)
        results = self._store.query(
            collection_name=self._collection,
            query_embedding=query_embedding,
            top_k=top_k,
        )

        documents: list[Document] = []
        for item in results:
            metadata = dict(item.get("metadata") or {})
            metadata["score"] = item.get("score")
            documents.append(
                Document(content=item.get("document", ""), metadata=metadata)
            )
        logger.info("检索完成", query=query, result_count=len(documents))
        return documents
