"""
Chroma 向量存储封装。
基于 chromadb PersistentClient 提供进程内持久化的向量库，
供 RAG 检索和长期记忆共用。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.config.logging import get_logger

logger = get_logger(__name__)


class ChromaStore:
    """
    Chroma 向量库封装。

    使用 chromadb.PersistentClient 将向量持久化到本地目录，
    无需外部服务。通过 collection 名称隔离不同用途的向量集合。
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

    def query(
        self,
        collection_name: str,
        query_embedding: list[float],
        top_k: int = 5,
    ) -> list[dict[str, Any]]:
        """
        按向量相似度检索。

        Args:
            collection_name: collection 名称。
            query_embedding: 查询向量。
            top_k: 返回最相似的 top_k 条。

        Returns:
            结果列表，每项含 id、document、metadata、score。
        """
        collection = self.get_or_create_collection(collection_name)
        count = collection.count()
        if count == 0:
            return []

        result = collection.query(
            query_embeddings=[query_embedding],
            n_results=min(top_k, count),
        )

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
