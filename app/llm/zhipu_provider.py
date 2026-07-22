"""
智谱 GLM LLM Provider 实现。
通过 OpenAI Compatible API 接入智谱大模型，使用 langchain-openai 的 ChatOpenAI。
"""

from langchain_openai import ChatOpenAI
from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config.settings import Settings
from app.llm.base import BaseLLMProvider


class ZhipuProvider(BaseLLMProvider):
    """
    智谱 GLM Provider。

    通过 OpenAI Compatible API 接入智谱大模型。
    仅需修改 base_url 和 api_key 即可复用 LangChain 完整生态。

    支持模型：glm-4, glm-4-plus, glm-4-long, glm-4v 等。
    """

    def __init__(self, settings: Settings):
        self._settings = settings

    def get_chat_model(self, **kwargs) -> ChatOpenAI:
        """
        返回 LangChain ChatOpenAI 实例，已配置为智谱 API。

        Args:
            **kwargs: 可选覆盖参数：
                - model (str): 模型名称
                - temperature (float): 温度
                - max_tokens (int): 最大输出 token 数

        Returns:
            ChatOpenAI 实例。
        """
        return ChatOpenAI(
            model=kwargs.get("model", self._settings.ZHIPU_MODEL),
            api_key=self._settings.ZHIPU_API_KEY,
            base_url=self._settings.ZHIPU_BASE_URL,
            temperature=kwargs.get("temperature", self._settings.ZHIPU_TEMPERATURE),
            max_tokens=kwargs.get("max_tokens", self._settings.ZHIPU_MAX_TOKENS),
        )

    def get_client(self) -> OpenAI:
        """
        返回 OpenAI SDK Client，已配置为智谱 API。
        用于非 LangChain 场景的直接调用。
        """
        return OpenAI(
            api_key=self._settings.ZHIPU_API_KEY,
            base_url=self._settings.ZHIPU_BASE_URL,
        )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
    )
    def chat_completion(self, messages: list[dict], **kwargs) -> str:
        """
        带重试机制的同步聊天完成方法。

        Args:
            messages: 消息列表，格式为 [{"role": "user", "content": "..."}]
            **kwargs: 传递给 OpenAI API 的额外参数。

        Returns:
            模型回复文本。
        """
        client = self.get_client()
        response = client.chat.completions.create(
            model=kwargs.get("model", self._settings.ZHIPU_MODEL),
            messages=messages,
            temperature=kwargs.get("temperature", self._settings.ZHIPU_TEMPERATURE),
            max_tokens=kwargs.get("max_tokens", self._settings.ZHIPU_MAX_TOKENS),
        )
        return response.choices[0].message.content
