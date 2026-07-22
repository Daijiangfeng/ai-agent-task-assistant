"""
智谱 GLM LLM Provider 实现。
通过 Anthropic Compatible API 接入智谱大模型，使用 langchain-anthropic 的 ChatAnthropic。
鉴权方式：Authorization: Bearer <ANTHROPIC_AUTH_TOKEN>
API Key 申请地址：https://open.bigmodel.cn
"""

from anthropic import Anthropic
from langchain_anthropic import ChatAnthropic
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config.settings import Settings
from app.llm.base import BaseLLMProvider

# Anthropic SDK 要求 api_key 非空；此处用作 x-api-key 占位，
# 真实鉴权走 default_headers 中的 Authorization: Bearer 头。
_BEARER_PLACEHOLDER = "zhipu-bearer"


class ZhipuProvider(BaseLLMProvider):
    """
    智谱 GLM Provider（Anthropic 兼容端点）。

    通过 Anthropic Compatible API 接入智谱大模型。
    使用 Authorization: Bearer 头鉴权，token 由 ANTHROPIC_AUTH_TOKEN 配置。

    支持模型：glm-4, glm-4-plus, glm-4-long, glm-4v, glm-5.2 等。
    """

    def __init__(self, settings: Settings):
        self._settings = settings

    def _bearer_headers(self) -> dict[str, str]:
        """构造 Authorization: Bearer 请求头。"""
        return {"Authorization": f"Bearer {self._settings.ANTHROPIC_AUTH_TOKEN}"}

    def get_chat_model(self, **kwargs) -> ChatAnthropic:
        """
        返回 LangChain ChatAnthropic 实例，已配置为智谱 Anthropic 兼容端点。

        鉴权走 Authorization: Bearer 头；x-api-key 仅作为 SDK 必填占位。

        Args:
            **kwargs: 可选覆盖参数：
                - model (str): 模型名称
                - temperature (float): 温度
                - max_tokens (int): 最大输出 token 数

        Returns:
            ChatAnthropic 实例。
        """
        return ChatAnthropic(
            model=kwargs.get("model", self._settings.ZHIPU_MODEL),
            anthropic_api_key=_BEARER_PLACEHOLDER,
            anthropic_api_url=self._settings.ANTHROPIC_BASE_URL,
            default_headers=self._bearer_headers(),
            temperature=kwargs.get("temperature", self._settings.ZHIPU_TEMPERATURE),
            max_tokens=kwargs.get("max_tokens", self._settings.ZHIPU_MAX_TOKENS),
        )

    def get_client(self) -> Anthropic:
        """
        返回 Anthropic SDK Client，已配置为智谱 Anthropic 兼容端点。
        使用 Authorization: Bearer 头鉴权，用于非 LangChain 场景的直接调用。
        """
        return Anthropic(
            api_key=_BEARER_PLACEHOLDER,
            base_url=self._settings.ANTHROPIC_BASE_URL,
            default_headers=self._bearer_headers(),
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
            **kwargs: 传递给 Anthropic API 的额外参数。

        Returns:
            模型回复文本。
        """
        client = self.get_client()
        # Anthropic SDK 要求 system 消息单独传递；此处将 messages 中 role=system 的提取出来
        system_msgs = [m["content"] for m in messages if m.get("role") == "system"]
        non_system = [m for m in messages if m.get("role") != "system"]
        system_prompt = "\n".join(system_msgs) if system_msgs else None

        create_kwargs = {
            "model": kwargs.get("model", self._settings.ZHIPU_MODEL),
            "messages": non_system,
            "temperature": kwargs.get("temperature", self._settings.ZHIPU_TEMPERATURE),
            "max_tokens": kwargs.get("max_tokens", self._settings.ZHIPU_MAX_TOKENS),
        }
        if system_prompt:
            create_kwargs["system"] = system_prompt

        response = client.messages.create(**create_kwargs)
        return response.content[0].text
