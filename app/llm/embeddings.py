"""
Embedding Provider 抽象层。
定义文本向量化的统一接口，供 Memory 和 RAG 模块共用。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from langchain_openai import OpenAIEmbeddings
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config.settings import Settings


class BaseEmbeddingProvider(ABC):
    """
    Embedding Provider 抽象基类。

    所有向量化实现必须提供批量文档向量化和单条查询向量化两个接口。
    """

    @abstractmethod
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """
        批量将文档文本向量化。

        Args:
            texts: 文档文本列表。

        Returns:
            向量列表，与输入一一对应。
        """
        ...

    @abstractmethod
    def embed_query(self, text: str) -> list[float]:
        """
        将单条查询文本向量化。

        Args:
            text: 查询文本。

        Returns:
            向量。
        """
        ...


class ZhipuEmbeddingProvider(BaseEmbeddingProvider):
    """
    智谱 Embedding Provider。

    通过 OpenAI Compatible API 接入智谱 embedding-3 模型，
    复用 ANTHROPIC_AUTH_TOKEN 与 ZHIPU_OPENAI_BASE_URL。
    """

    def __init__(self, settings: Settings):
        self._settings = settings
        self._client = OpenAIEmbeddings(
            model=settings.ZHIPU_EMBEDDING_MODEL,
            api_key=settings.ANTHROPIC_AUTH_TOKEN,
            base_url=settings.ZHIPU_OPENAI_BASE_URL,
            check_embedding_ctx_length=False,
        )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """批量向量化文档，失败自动重试。"""
        return self._client.embed_documents(texts)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    def embed_query(self, text: str) -> list[float]:
        """向量化单条查询，失败自动重试。"""
        return self._client.embed_query(text)
