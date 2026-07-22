"""
LLM Provider 单元测试。
测试 Provider 工厂、智谱 Provider 初始化等。
"""

import pytest

from app.config.settings import Settings
from app.llm.base import BaseLLMProvider
from app.llm.factory import LLMProviderFactory
from app.llm.zhipu_provider import ZhipuProvider


class TestLLMProviderFactory:
    """LLM Provider 工厂测试。"""

    def test_create_zhipu_provider(self, test_settings: Settings):
        """测试创建智谱 Provider。"""
        provider = LLMProviderFactory.create(
            provider_name="zhipu", settings=test_settings
        )
        assert isinstance(provider, ZhipuProvider)
        assert isinstance(provider, BaseLLMProvider)

    def test_create_invalid_provider(self, test_settings: Settings):
        """测试创建不支持的 Provider 抛出异常。"""
        with pytest.raises(ValueError, match="不支持的 LLM Provider"):
            LLMProviderFactory.create(
                provider_name="invalid", settings=test_settings
            )

    def test_register_custom_provider(self, test_settings: Settings):
        """测试注册自定义 Provider。"""

        class MockProvider(BaseLLMProvider):
            def __init__(self, settings):
                pass

            def get_chat_model(self, **kwargs):
                return None

            def get_client(self):
                return None

        LLMProviderFactory.register("mock", MockProvider)
        provider = LLMProviderFactory.create(
            provider_name="mock", settings=test_settings
        )
        assert isinstance(provider, MockProvider)

        # 清理
        del LLMProviderFactory._providers["mock"]


class TestZhipuProvider:
    """智谱 Provider 测试。"""

    def test_get_chat_model(self, test_settings: Settings):
        """测试获取 ChatModel 实例。"""
        provider = ZhipuProvider(test_settings)
        model = provider.get_chat_model()
        assert model is not None

    def test_get_chat_model_with_overrides(self, test_settings: Settings):
        """测试带参数覆盖的 ChatModel。"""
        provider = ZhipuProvider(test_settings)
        model = provider.get_chat_model(
            model="glm-4-long",
            temperature=0.1,
            max_tokens=8192,
        )
        assert model is not None

    def test_get_client(self, test_settings: Settings):
        """测试获取 OpenAI SDK Client。"""
        provider = ZhipuProvider(test_settings)
        client = provider.get_client()
        assert client is not None
