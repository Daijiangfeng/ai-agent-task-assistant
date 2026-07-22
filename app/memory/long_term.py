"""
长期记忆实现。
基于 Chroma 向量库保存用户偏好、历史任务摘要，支持语义检索。
"""

from __future__ import annotations

import time
from typing import Any, Optional

from app.config.logging import get_logger
from app.config.settings import Settings, get_settings
from app.llm.embeddings import BaseEmbeddingProvider
from app.llm.factory import create_embedding_provider
from app.memory.base import BaseMemory
from app.rag.vector_store import ChromaStore

logger = get_logger(__name__)

LONG_TERM_COLLECTION = "long_term_memory"


class VectorLongTermMemory(BaseMemory):
    """
    基于向量库的长期记忆。

    save() 将文本内容向量化后存入 Chroma，以 key 作为记录 ID；
    search() 做语义检索返回 top_k 条相关记忆。
    适合保存用户偏好、历史任务摘要等需要按语义召回的内容。
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
        self._collection = LONG_TERM_COLLECTION

    async def save(self, key: str, value: Any, ttl: int | None = None) -> None:
        """
        保存一条长期记忆。

        Args:
            key: 记忆 ID（同一 key 会被覆盖）。
            value: 记忆内容，非字符串会转成字符串向量化。
            ttl: 长期记忆忽略 ttl 参数。
        """
        text = value if isinstance(value, str) else str(value)
        embedding = self._embedding.embed_query(text)
        # 先删除同 ID 旧记录，避免 Chroma add 重复 ID 报错
        self._store.delete(self._collection, [key])
        self._store.add(
            collection_name=self._collection,
            ids=[key],
            embeddings=[embedding],
            documents=[text],
            metadatas=[{"key": key, "created_at": time.time()}],
        )

    async def get(self, key: str) -> Optional[Any]:
        """
        按 key 精确获取记忆内容。

        Args:
            key: 记忆 ID。

        Returns:
            记忆文本，不存在返回 None。
        """
        collection = self._store.get_or_create_collection(self._collection)
        result = collection.get(ids=[key])
        documents = result.get("documents") or []
        if documents:
            return documents[0]
        return None

    async def delete(self, key: str) -> None:
        """删除指定 key 的长期记忆。"""
        self._store.delete(self._collection, [key])

    async def search(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        """
        语义检索长期记忆。

        Args:
            query: 查询文本。
            top_k: 返回最相关的 top_k 条。

        Returns:
            记忆列表，每项含 key、value、score。
        """
        embedding = self._embedding.embed_query(query)
        results = self._store.query(
            collection_name=self._collection,
            query_embedding=embedding,
            top_k=top_k,
        )
        return [
            {
                "key": item.get("id"),
                "value": item.get("document"),
                "score": item.get("score"),
            }
            for item in results
        ]
