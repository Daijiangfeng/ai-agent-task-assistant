"""
RAG 系统抽象基类。
定义文档检索和索引的统一接口。
"""

from abc import ABC, abstractmethod
from typing import Any


class Document:
    """文档数据类。"""

    def __init__(self, content: str, metadata: dict[str, Any] | None = None):
        self.content = content
        self.metadata = metadata or {}


class BaseRetriever(ABC):
    """
    检索器抽象基类。
    负责从向量数据库中检索相关文档。
    """

    @abstractmethod
    async def retrieve(self, query: str, top_k: int = 5) -> list[Document]:
        """
        检索相关文档。

        Args:
            query: 查询文本。
            top_k: 返回最相关的 top_k 个文档。

        Returns:
            Document 列表。
        """
        ...


class BaseIndexer(ABC):
    """
    索引器抽象基类。
    负责将文档向量化并存入向量数据库。
    """

    @abstractmethod
    async def index(self, documents: list[Document]) -> None:
        """
        索引文档列表。

        Args:
            documents: 待索引的文档列表。
        """
        ...

    @abstractmethod
    async def delete(self, document_ids: list[str]) -> None:
        """
        删除已索引的文档。

        Args:
            document_ids: 文档 ID 列表。
        """
        ...
