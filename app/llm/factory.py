"""
LLM Provider 工厂模块。
根据配置创建对应的 LLM Provider 实例，支持未来扩展其他模型。
"""

from app.config.settings import Settings, get_settings
from app.llm.base import BaseLLMProvider
from app.llm.embeddings import BaseEmbeddingProvider, ZhipuEmbeddingProvider
from app.llm.zhipu_provider import ZhipuProvider


class LLMProviderFactory:
    """
    LLM Provider 工厂类。

    通过 provider_name 参数选择具体的 LLM 供应商实现。
    当前支持：zhipu（智谱 GLM）。
    未来可扩展：openai, deepseek, qwen 等。
    """

    _providers: dict[str, type[BaseLLMProvider]] = {
        "zhipu": ZhipuProvider,
    }

    @classmethod
    def create(
        cls,
        provider_name: str = "zhipu",
        settings: Settings | None = None,
    ) -> BaseLLMProvider:
        """
        创建 LLM Provider 实例。

        Args:
            provider_name: 供应商名称，默认 "zhipu"。
            settings: 配置实例，默认使用全局配置。

        Returns:
            BaseLLMProvider 实例。

        Raises:
            ValueError: 不支持的供应商名称。
        """
        provider_cls = cls._providers.get(provider_name)
        if not provider_cls:
            available = ", ".join(cls._providers.keys())
            raise ValueError(
                f"不支持的 LLM Provider: '{provider_name}'。"
                f"可用选项: {available}"
            )
        return provider_cls(settings=settings or get_settings())

    @classmethod
    def register(cls, name: str, provider_cls: type[BaseLLMProvider]) -> None:
        """
        注册新的 LLM Provider。

        Args:
            name: 供应商名称。
            provider_cls: Provider 类（必须继承 BaseLLMProvider）。
        """
        cls._providers[name] = provider_cls


class EmbeddingProviderFactory:
    """
    Embedding Provider 工厂类。

    当前支持：zhipu（智谱 embedding-3）。
    """

    _providers: dict[str, type[BaseEmbeddingProvider]] = {
        "zhipu": ZhipuEmbeddingProvider,
    }

    @classmethod
    def create(
        cls,
        provider_name: str = "zhipu",
        settings: Settings | None = None,
    ) -> BaseEmbeddingProvider:
        """
        创建 Embedding Provider 实例。

        Args:
            provider_name: 供应商名称，默认 "zhipu"。
            settings: 配置实例，默认使用全局配置。

        Returns:
            BaseEmbeddingProvider 实例。

        Raises:
            ValueError: 不支持的供应商名称。
        """
        provider_cls = cls._providers.get(provider_name)
        if not provider_cls:
            available = ", ".join(cls._providers.keys())
            raise ValueError(
                f"不支持的 Embedding Provider: '{provider_name}'。"
                f"可用选项: {available}"
            )
        return provider_cls(settings=settings or get_settings())


def create_embedding_provider(
    settings: Settings | None = None,
) -> BaseEmbeddingProvider:
    """便捷函数：创建默认（智谱）Embedding Provider。"""
    return EmbeddingProviderFactory.create(settings=settings)
