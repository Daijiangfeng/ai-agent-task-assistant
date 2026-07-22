"""
Pytest fixtures 和测试配置。
提供测试用的 Settings、mock LLM Provider 等。
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.settings import Settings
from app.tools.registry import ToolRegistry


@pytest.fixture
def test_settings() -> Settings:
    """创建测试用配置实例。"""
    return Settings(
        ANTHROPIC_AUTH_TOKEN="test_key",
        ANTHROPIC_BASE_URL="https://open.bigmodel.cn/api/anthropic",
        ZHIPU_OPENAI_BASE_URL="https://open.bigmodel.cn/api/paas/v4/",
        ZHIPU_MODEL="glm-4-plus",
        DEBUG=True,
        MAX_REPLAN_ITERATIONS=2,
        MAX_EXECUTION_STEPS=5,
    )


@pytest.fixture
def sample_goal() -> str:
    """示例用户目标。"""
    return "帮我总结 Python 异步编程的最佳实践"


@pytest.fixture
def sample_context() -> str:
    """示例上下文。"""
    return "面向有经验的 Python 开发者"


@pytest.fixture
def mock_llm_provider():
    """创建 mock LLM Provider，避免调用真实 API。"""
    from app.llm.base import BaseLLMProvider

    provider = MagicMock(spec=BaseLLMProvider)

    # mock ChatModel
    mock_chat_model = MagicMock()
    mock_response = MagicMock()
    mock_response.content = (
        '{"goal": "test", "subtasks": [{"id": "task_1", '
        '"description": "test task", "dependencies": [], "tool": null}]}'
    )
    mock_response.tool_calls = []

    # 让 ainvoke 返回 mock 响应
    mock_chat_model.ainvoke = AsyncMock(return_value=mock_response)
    mock_chat_model.bind_tools = MagicMock(return_value=mock_chat_model)
    provider.get_chat_model = MagicMock(return_value=mock_chat_model)
    provider.get_client = MagicMock(return_value=MagicMock())

    return provider


@pytest.fixture(autouse=True)
def reset_tool_registry():
    """每个测试前后重置工具注册表。"""
    ToolRegistry.clear()
    yield
    ToolRegistry.clear()


@pytest.fixture
def temp_chroma_dir(tmp_path):
    """临时 Chroma 持久化目录。"""
    d = tmp_path / "chroma"
    d.mkdir(parents=True, exist_ok=True)
    return str(d)


@pytest.fixture
def mock_embedding_provider():
    """
    确定性 mock embedding provider。

    基于文本哈希生成固定维度向量，避免调用真实嵌入 API，
    相同文本返回相同向量，保证检索可重复。
    """
    import hashlib

    from app.llm.embeddings import BaseEmbeddingProvider

    dim = 16

    def _embed(text: str) -> list[float]:
        digest = hashlib.md5(text.encode("utf-8")).digest()
        # 循环填充到 dim 维，归一化到 [0,1)
        return [digest[i % len(digest)] / 255.0 for i in range(dim)]

    class MockEmbeddingProvider(BaseEmbeddingProvider):
        def embed_documents(self, texts: list[str]) -> list[list[float]]:
            return [_embed(t) for t in texts]

        def embed_query(self, text: str) -> list[float]:
            return _embed(text)

    return MockEmbeddingProvider()

