"""
LLM Provider 抽象基类。
定义所有 LLM 供应商必须实现的统一接口。
"""

from abc import ABC, abstractmethod

from langchain_core.language_models.chat_models import BaseChatModel


class BaseLLMProvider(ABC):
    """
    LLM Provider 抽象基类。

    所有 LLM 供应商（智谱、OpenAI 等）必须继承此类并实现以下方法：
    - get_chat_model(): 返回 LangChain ChatModel 实例，用于 Agent 调用
    - get_client(): 返回原始 SDK Client，用于非 LangChain 场景
    """

    @abstractmethod
    def get_chat_model(self, **kwargs) -> BaseChatModel:
        """
        返回 LangChain ChatModel 实例。

        Args:
            **kwargs: 可选参数覆盖默认配置（如 model, temperature, max_tokens）。

        Returns:
            LangChain BaseChatModel 实例。
        """
        ...

    @abstractmethod
    def get_client(self):
        """
        返回原始 SDK Client 实例。
        用于需要直接 SDK 调用的场景（如 streaming、function calling 调试）。
        """
        ...
