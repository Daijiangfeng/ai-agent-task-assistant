"""
智谱 GLM LLM Provider 实现。
通过 Anthropic Compatible API 接入智谱大模型，使用 langchain-anthropic 的 ChatAnthropic。
鉴权方式：Authorization: Bearer <ANTHROPIC_AUTH_TOKEN>
API Key 申请地址：https://open.bigmodel.cn
"""

from anthropic import Anthropic, AsyncAnthropic
from langchain_anthropic import ChatAnthropic
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config.settings import Settings
from app.llm.base import BaseLLMProvider, resolve_bearer_token

# 真实鉴权走 default_headers 中的 Authorization: Bearer <token> 头；
# Anthropic SDK 强制要求 api_key 非空，此常量仅作为 x-api-key 占位，不参与鉴权。
_SDK_API_KEY_PLACEHOLDER = "unused-x-api-key"


class ZhipuProvider(BaseLLMProvider):
    """
    智谱 GLM Provider（Anthropic 兼容端点）。

    通过 Anthropic Compatible API 接入智谱大模型。
    使用 Authorization: Bearer 头鉴权，token 由 ANTHROPIC_AUTH_TOKEN 配置。

    支持模型：glm-4.5-air, glm-4, glm-4-plus, glm-4-long, glm-4v 等。
    """

    def __init__(self, settings: Settings):
        self._settings = settings

    def _resolve_token(self) -> str:
        """
        读取并校验 Bearer token。

        为空时快速失败，避免向智谱端点发出空的 Authorization 头导致难以定位的 401。

        Raises:
            ValueError: ANTHROPIC_AUTH_TOKEN 未配置或为空。
        """
        return resolve_bearer_token(
            self._settings.ANTHROPIC_AUTH_TOKEN,
            error_message=(
                "ANTHROPIC_AUTH_TOKEN 未配置：智谱 Anthropic 兼容端点需要该 token "
                "走 Authorization: Bearer 鉴权，请在 .env 中设置 ANTHROPIC_AUTH_TOKEN。"
            ),
        )

    def _bearer_headers(self) -> dict[str, str]:
        """构造标准 Authorization: Bearer 鉴权头。"""
        return {"Authorization": f"Bearer {self._resolve_token()}"}

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
            anthropic_api_key=_SDK_API_KEY_PLACEHOLDER,
            anthropic_api_url=self._settings.ANTHROPIC_BASE_URL,
            default_headers=self._bearer_headers(),
            temperature=kwargs.get("temperature", self._settings.ZHIPU_TEMPERATURE),
            max_tokens=kwargs.get("max_tokens", self._settings.ZHIPU_MAX_TOKENS),
        )

    def get_client(self) -> Anthropic:
        """
        返回同步 Anthropic SDK Client，已配置为智谱 Anthropic 兼容端点。
        使用 Authorization: Bearer 头鉴权，用于非 LangChain 场景的直接调用。

        注意：async 上下文请使用 get_async_client()，避免阻塞事件循环。
        """
        return Anthropic(
            api_key=_SDK_API_KEY_PLACEHOLDER,
            base_url=self._settings.ANTHROPIC_BASE_URL,
            default_headers=self._bearer_headers(),
        )

    def get_async_client(self) -> AsyncAnthropic:
        """
        返回异步 AsyncAnthropic SDK Client，已配置为智谱 Anthropic 兼容端点。

        使用 Authorization: Bearer 头鉴权，用于 async 场景的直接调用
        与 SDK 级流式响应（messages.stream），不阻塞事件循环。
        """
        return AsyncAnthropic(
            api_key=_SDK_API_KEY_PLACEHOLDER,
            base_url=self._settings.ANTHROPIC_BASE_URL,
            default_headers=self._bearer_headers(),
        )

    def _build_create_kwargs(self, messages: list[dict], **kwargs) -> dict:
        """
        构造 messages.create 请求参数（同步/异步共用）。

        Anthropic SDK 要求 system 消息单独传递；此处将 messages 中
        role=system 的提取出来，其余参数从 settings 取默认值。
        """
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
        return create_kwargs

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
    )
    def chat_completion(self, messages: list[dict], **kwargs) -> str:
        """
        带重试机制的同步聊天完成方法。

        注意：async 上下文请使用 achat_completion()，避免阻塞事件循环。

        Args:
            messages: 消息列表，格式为 [{"role": "user", "content": "..."}]
            **kwargs: 传递给 Anthropic API 的额外参数。

        Returns:
            模型回复文本。
        """
        client = self.get_client()
        response = client.messages.create(**self._build_create_kwargs(messages, **kwargs))
        return response.content[0].text

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
    )
    async def achat_completion(self, messages: list[dict], **kwargs) -> str:
        """
        带重试机制的异步聊天完成方法（async 场景首选）。

        Args:
            messages: 消息列表，格式为 [{"role": "user", "content": "..."}]
            **kwargs: 传递给 Anthropic API 的额外参数。

        Returns:
            模型回复文本。
        """
        client = self.get_async_client()
        response = await client.messages.create(
            **self._build_create_kwargs(messages, **kwargs)
        )
        return response.content[0].text

