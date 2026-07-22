"""
Chroma 索引器。
将文档解析、分块、向量化后写入 Chroma 向量库。
"""

from __future__ import annotations

from app.config.logging import get_logger
from app.llm.embeddings import BaseEmbeddingProvider
from app.rag.base import BaseIndexer, Document
from app.rag.loader import DocumentLoader
from app.rag.splitter import TextSplitter
from app.rag.vector_store import ChromaStore

logger = get_logger(__name__)

RAG_COLLECTION = "rag_documents"


class ChromaIndexer(BaseIndexer):
    """
    Chroma 索引器。

    流程：Document -> split -> embed(智谱) -> Chroma collection `rag_documents`。
    """

    def __init__(
        self,
        embedding_provider: BaseEmbeddingProvider,
        vector_store: ChromaStore,
        splitter: TextSplitter | None = None,
        collection_name: str = RAG_COLLECTION,
    ):
        self._embedding = embedding_provider
        self._store = vector_store
        self._splitter = splitter or TextSplitter()
        self._collection = collection_name

    async def index(self, documents: list[Document]) -> list[str]:
        """
        索引文档列表（先分块再向量化入库）。

        Args:
            documents: 待索引的原始文档列表。

        Returns:
            已写入的 chunk_id 列表。
        """
        if not documents:
            return []

        chunks = self._splitter.split(documents)
        if not chunks:
            return []

        texts = [c.content for c in chunks]
        ids = [c.metadata["chunk_id"] for c in chunks]
        metadatas = [c.metadata for c in chunks]

        embeddings = self._embedding.embed_documents(texts)

        self._store.add(
            collection_name=self._collection,
            ids=ids,
            embeddings=embeddings,
            documents=texts,
            metadatas=metadatas,
        )
        logger.info("索引完成", chunk_count=len(ids), collection=self._collection)
        return ids

    async def index_file(self, file_path: str) -> list[str]:
        """
        加载并索引单个文件。

        Args:
            file_path: 文件路径。

        Returns:
            已写入的 chunk_id 列表。
        """
        documents = DocumentLoader.load(file_path)
        return await self.index(documents)

    async def delete(self, document_ids: list[str]) -> None:
        """
        删除已索引的 chunk。

        Args:
            document_ids: chunk_id 列表。
        """
        if document_ids:
            self._store.delete(self._collection, document_ids)
            logger.info("删除索引", count=len(document_ids))
