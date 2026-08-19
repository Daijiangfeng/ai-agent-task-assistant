"""
长期记忆实现。
基于向量库保存用户偏好、历史任务摘要，支持语义检索。

数据隔离：每条记忆的元数据携带 user_id / tenant_id，记录 ID 以
"tenant:user:key" 复合形式命名，且 get/delete/search 强制按作用域过滤，
杜绝跨租户/跨用户召回（用户 A 的记忆不会被用户 B 检索到）。
"""

from __future__ import annotations

import time
from typing import Any

from app.config.logging import get_logger
from app.config.settings import Settings, get_settings
from app.llm.embeddings import BaseEmbeddingProvider
from app.llm.factory import create_embedding_provider
from app.memory.base import DEFAULT_TENANT_ID, DEFAULT_USER_ID, BaseMemory
from app.memory.vector_store import BaseVectorStore, create_vector_store

logger = get_logger(__name__)

LONG_TERM_COLLECTION = "long_term_memory"
LONG_TERM_NAMESPACE = "long_term_memory"


class VectorLongTermMemory(BaseMemory):
    """
    基于向量库的长期记忆（按 tenant_id + user_id 强隔离）。

    save() 将文本内容向量化后存入向量库，以 "tenant:user:key" 作为记录 ID，
    元数据写入 user_id / tenant_id / namespace；
    search() 仅在当前作用域内做语义检索，返回 top_k 条相关记忆。
    """

    def __init__(
        self,
        settings: Settings | None = None,
        embedding_provider: BaseEmbeddingProvider | None = None,
        vector_store: BaseVectorStore | None = None,
    ):
        self._settings = settings or get_settings()
        self._embedding = embedding_provider or create_embedding_provider(
            self._settings
        )
        self._store = vector_store or create_vector_store(self._settings)
        self._collection = LONG_TERM_COLLECTION

    def _record_id(self, key: str, user_id: str, tenant_id: str) -> str:
        """复合记录 ID：保证跨租户/跨用户 key 不冲突。"""
        return f"{tenant_id}:{user_id}:{key}"

    def _scope_where(self, user_id: str, tenant_id: str) -> dict[str, str]:
        """查询过滤条件：仅召回当前用户在当前租户下的记忆。"""
        return {
            "user_id": user_id,
            "tenant_id": tenant_id,
            "namespace": LONG_TERM_NAMESPACE,
        }

    async def save(
        self,
        key: str,
        value: Any,
        ttl: int | None = None,
        *,
        user_id: str = DEFAULT_USER_ID,
        tenant_id: str = DEFAULT_TENANT_ID,
    ) -> None:
        """
        保存一条长期记忆。

        Args:
            key: 记忆 ID（同一作用域内同 key 会被覆盖）。
            value: 记忆内容，非字符串会转成字符串向量化。
            ttl: 长期记忆忽略 ttl 参数。
            user_id: 所有者用户 ID，写入元数据用于隔离。
            tenant_id: 所属租户 ID，写入元数据用于隔离。
        """
        text = value if isinstance(value, str) else str(value)
        embedding = await self._embedding.aembed_query(text)
        record_id = self._record_id(key, user_id, tenant_id)
        # 先删除同 ID 旧记录，避免向量库 add 重复 ID 报错
        self._store.delete(self._collection, [record_id])
        self._store.add(
            collection_name=self._collection,
            ids=[record_id],
            embeddings=[embedding],
            documents=[text],
            metadatas=[
                {
                    "key": key,
                    "user_id": user_id,
                    "tenant_id": tenant_id,
                    "namespace": LONG_TERM_NAMESPACE,
                    "created_at": time.time(),
                }
            ],
        )

    async def get(
        self,
        key: str,
        *,
        user_id: str = DEFAULT_USER_ID,
        tenant_id: str = DEFAULT_TENANT_ID,
    ) -> Any | None:
        """
        按 key 精确获取记忆（仅限当前作用域，跨作用域返回 None）。

        Args:
            key: 记忆 ID。
            user_id: 所有者用户 ID。
            tenant_id: 所属租户 ID。

        Returns:
            记忆文本，不存在或不属于当前作用域返回 None。
        """
        record_id = self._record_id(key, user_id, tenant_id)
        results = self._store.get(
            self._collection,
            [record_id],
            where=self._scope_where(user_id, tenant_id),
        )
        if results and results[0].get("document"):
            return results[0]["document"]
        return None

    async def delete(
        self,
        key: str,
        *,
        user_id: str = DEFAULT_USER_ID,
        tenant_id: str = DEFAULT_TENANT_ID,
    ) -> None:
        """删除当前作用域下指定 key 的长期记忆。"""
        record_id = self._record_id(key, user_id, tenant_id)
        self._store.delete(self._collection, [record_id])

    async def search(
        self,
        query: str,
        top_k: int = 5,
        *,
        user_id: str = DEFAULT_USER_ID,
        tenant_id: str = DEFAULT_TENANT_ID,
    ) -> list[dict[str, Any]]:
        """
        语义检索长期记忆（强制限定在 user_id + tenant_id 作用域内）。

        Args:
            query: 查询文本。
            top_k: 返回最相关的 top_k 条。
            user_id: 所有者用户 ID，仅在该用户记忆内检索。
            tenant_id: 所属租户 ID，仅在该租户记忆内检索。

        Returns:
            记忆列表，每项含 key、value、score。
        """
        embedding = await self._embedding.aembed_query(query)
        results = self._store.query(
            collection_name=self._collection,
            query_embedding=embedding,
            top_k=top_k,
            where=self._scope_where(user_id, tenant_id),
        )
        return [
            {
                "key": item.get("id"),
                "value": item.get("document"),
                "score": item.get("score"),
            }
            for item in results
        ]
