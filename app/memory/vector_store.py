"""
向量存储抽象层（长期记忆的后端存储）。

- BaseVectorStore: 统一接口，长期记忆（VectorLongTermMemory）只依赖该抽象，
  后端可插拔（chroma / pgvector / 未来 Milvus、Qdrant）。
- ChromaStore: 基于 chromadb PersistentClient 的进程内持久化实现，
  适合开发与单机 Demo。
- create_vector_store: 按配置创建后端实例。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from app.config.logging import get_logger
from app.config.settings import Settings, get_settings

logger = get_logger(__name__)


class BaseVectorStore(ABC):
    """
    向量库统一接口。

    实现约定：
    - 所有操作按 collection_name 逻辑隔离（Chroma 用 collection，pgvector 用表分区键）；
    - query 支持 where 条件过滤（等值匹配），用于租户/用户级数据隔离；
    - get 按 ID 精确读取，同样支持 where 过滤（防御性双重隔离）。
    """

    @abstractmethod
    def add(
        self,
        collection_name: str,
        ids: list[str],
        embeddings: list[list[float]],
        documents: list[str],
        metadatas: list[dict[str, Any]] | None = None,
    ) -> None:
        """向指定 collection 添加/覆盖向量记录。"""
        ...

    @abstractmethod
    def get(
        self,
        collection_name: str,
        ids: list[str],
        where: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """按 ID 精确读取记录，每项含 id、document、metadata。"""
        ...

    @abstractmethod
    def query(
        self,
        collection_name: str,
        query_embedding: list[float],
        top_k: int = 5,
        where: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """按向量相似度检索（可附加 where 等值过滤）。"""
        ...

    @abstractmethod
    def delete(self, collection_name: str, ids: list[str]) -> None:
        """删除指定 ID 的向量记录。"""
        ...

    @abstractmethod
    def count(self, collection_name: str) -> int:
        """返回 collection 中的向量记录总数。"""
        ...

    @abstractmethod
    def list_records(
        self,
        collection_name: str,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """列出记录（不做向量检索，仅枚举元数据）。"""
        ...

    @abstractmethod
    def delete_by_source(self, collection_name: str, source: str) -> int:
        """按来源（metadata.source）删除一个文档的所有记录，返回删除数。"""
        ...


class ChromaStore(BaseVectorStore):
    """
    Chroma 向量库实现（进程内持久化）。

    使用 chromadb.PersistentClient 将向量持久化到本地目录，
    无需外部服务，适合开发与单机部署；
    生产多实例部署请切换 pgvector 等分布式后端（VECTOR_STORE_BACKEND=pgvector）。
    """

    def __init__(self, persist_dir: str):
        """
        初始化 Chroma 客户端。

        Args:
            persist_dir: 持久化目录路径，不存在时自动创建。
        """
        import chromadb

        Path(persist_dir).mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(path=persist_dir)
        logger.info("ChromaStore 初始化完成", persist_dir=persist_dir)

    def get_or_create_collection(self, name: str):
        """
        获取或创建 collection。

        Args:
            name: collection 名称。

        Returns:
            chromadb Collection 实例。
        """
        return self._client.get_or_create_collection(
            name=name,
            metadata={"hnsw:space": "cosine"},
        )

    def add(
        self,
        collection_name: str,
        ids: list[str],
        embeddings: list[list[float]],
        documents: list[str],
        metadatas: list[dict[str, Any]] | None = None,
    ) -> None:
        """
        向指定 collection 添加向量记录。

        Args:
            collection_name: collection 名称。
            ids: 记录 ID 列表。
            embeddings: 向量列表。
            documents: 原始文本列表。
            metadatas: 元数据列表（可选）。
        """
        collection = self.get_or_create_collection(collection_name)
        collection.add(
            ids=ids,
            embeddings=embeddings,
            documents=documents,
            metadatas=metadatas,
        )

    @staticmethod
    def _to_chroma_where(where: dict[str, Any] | None) -> dict[str, Any] | None:
        """
        将扁平等值过滤 dict 转换为 Chroma where 语法。

        Chroma 要求 where 字典只能含一个顶层键（或 $and/$or 操作符），
        多字段等值需显式 $and 组合（如 {"$and": [{"a": 1}, {"b": 2}]}）。
        """
        if not where:
            return None
        if len(where) == 1:
            return dict(where)
        return {"$and": [{k: v} for k, v in where.items()]}

    def get(
        self,
        collection_name: str,
        ids: list[str],
        where: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """
        按 ID 精确读取记录。

        Args:
            collection_name: collection 名称。
            ids: 记录 ID 列表。
            where: 等值过滤条件（如 user_id/tenant_id），用于防御性隔离。

        Returns:
            记录列表，每项含 id、document、metadata。
        """
        collection = self.get_or_create_collection(collection_name)
        if not ids:
            return []
        kwargs: dict[str, Any] = {"ids": ids, "include": ["documents", "metadatas"]}
        chroma_where = self._to_chroma_where(where)
        if chroma_where:
            kwargs["where"] = chroma_where
        result = collection.get(**kwargs)
        return self._to_items(result)

    @staticmethod
    def _to_items(result: dict[str, Any]) -> list[dict[str, Any]]:
        """将 Chroma get 结果转换为通用记录列表。"""
        ids = result.get("ids", []) or []
        documents = result.get("documents", []) or []
        metadatas = result.get("metadatas", []) or []
        items: list[dict[str, Any]] = []
        for i, doc_id in enumerate(ids):
            items.append(
                {
                    "id": doc_id,
                    "document": documents[i] if i < len(documents) else "",
                    "metadata": metadatas[i] if i < len(metadatas) else {},
                }
            )
        return items

    def query(
        self,
        collection_name: str,
        query_embedding: list[float],
        top_k: int = 5,
        where: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """
        按向量相似度检索。

        Args:
            collection_name: collection 名称。
            query_embedding: 查询向量。
            top_k: 返回最相似的 top_k 条。
            where: 等值过滤条件（如 user_id/tenant_id），
                   实现租户/用户级数据隔离，跨作用域内容不会被召回。

        Returns:
            结果列表，每项含 id、document、metadata、score。
        """
        collection = self.get_or_create_collection(collection_name)
        count = collection.count()
        if count == 0:
            return []

        kwargs: dict[str, Any] = {
            "query_embeddings": [query_embedding],
            "n_results": min(top_k, count),
        }
        chroma_where = self._to_chroma_where(where)
        if chroma_where:
            kwargs["where"] = chroma_where

        result = collection.query(**kwargs)

        items: list[dict[str, Any]] = []
        ids = result.get("ids", [[]])[0]
        documents = result.get("documents", [[]])[0]
        metadatas = result.get("metadatas", [[]])[0]
        distances = result.get("distances", [[]])[0]

        for i, doc_id in enumerate(ids):
            distance = distances[i] if i < len(distances) else 0.0
            items.append(
                {
                    "id": doc_id,
                    "document": documents[i] if i < len(documents) else "",
                    "metadata": metadatas[i] if i < len(metadatas) else {},
                    # cosine 距离转相似度分数
                    "score": 1.0 - distance,
                }
            )
        return items

    def delete(self, collection_name: str, ids: list[str]) -> None:
        """
        删除指定 ID 的向量记录。

        Args:
            collection_name: collection 名称。
            ids: 待删除的记录 ID 列表。
        """
        collection = self.get_or_create_collection(collection_name)
        collection.delete(ids=ids)

    def count(self, collection_name: str) -> int:
        """返回 collection 中的向量记录（chunk）总数。"""
        collection = self.get_or_create_collection(collection_name)
        return collection.count()

    def list_records(
        self,
        collection_name: str,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """
        列出 collection 中的记录（不做向量检索，仅枚举元数据）。

        Args:
            collection_name: collection 名称。
            limit: 可选，返回数量上限。
            offset: 偏移量。

        Returns:
            记录列表，每项含 id、document、metadata。
        """
        collection = self.get_or_create_collection(collection_name)
        if collection.count() == 0:
            return []
        kwargs: dict[str, Any] = {"include": ["documents", "metadatas"]}
        if limit is not None:
            kwargs["limit"] = limit
            kwargs["offset"] = offset
        result = collection.get(**kwargs)
        return self._to_items(result)

    def delete_by_source(self, collection_name: str, source: str) -> int:
        """
        按来源（metadata.source）删除一个文档的所有 chunk。

        Returns:
            被删除的 chunk 数量。
        """
        collection = self.get_or_create_collection(collection_name)
        existing = collection.get(where={"source": source})
        ids = existing.get("ids", []) or []
        if ids:
            collection.delete(ids=ids)
        return len(ids)


def create_vector_store(settings: Settings | None = None) -> BaseVectorStore:
    """
    按配置创建向量库后端实例。

    默认 chroma（开发/单机）；生产多实例部署配置 VECTOR_STORE_BACKEND=pgvector
    使用 PostgreSQL 扩展（后续 Milvus/Qdrant 可在此扩展）。

    Args:
        settings: 配置对象，默认使用全局配置。

    Returns:
        BaseVectorStore 实例。

    Raises:
        ValueError: 配置了不支持的向量库后端。
    """
    settings = settings or get_settings()
    backend = settings.VECTOR_STORE_BACKEND.strip().lower()

    if backend == "chroma":
        return ChromaStore(settings.chroma_dir)

    if backend == "pgvector":
        from app.memory.vector_store_pg import PgVectorStore

        return PgVectorStore(settings)

    raise ValueError(
        f"不支持的向量库后端: {settings.VECTOR_STORE_BACKEND!r}（支持: chroma | pgvector）"
    )
