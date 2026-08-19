"""
Observe — HTTP 只读工具（http.get）。

Agent 通过它安全地发起 HTTP GET 请求获取公开信息。
安全设计：
- 仅允许 http/https 协议，拒绝 file:// / data: / ftp:// 等危险 scheme；
- 禁止访问回环/私网/保留网段（防 SSRF），除非显式配置 allow_internal；
- 强制超时、限制响应体大小（防止大响应 DoS）；
- 统一错误收敛，不外泄底层异常细节。
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from app.config.logging import get_logger
from app.config.settings import Settings, get_settings
from app.tools.base import BaseTool, ToolInput, ToolOutput
from app.tools.errors import ExternalServiceError, ValidationError
from app.tools.schema import ExecutionMode, ToolCategory
from app.tools.security import CATEGORY_NETWORK, ToolContext

logger = get_logger(__name__)

_ALLOWED_SCHEMES = {"http", "https"}
# SSRF 防护：默认禁止引用的私网/回环网段（IPv4）。IPv6 与域名解析后校验依赖 DNS，
# 此处对明确字面的保留地址做拦截，作为纵深防御第一层。
_BLOCKED_NETWORKS = ("127.", "10.", "192.168.", "169.254.", "0.")

_DEFAULT_TIMEOUT = 10.0
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024  # 2 MiB


def _validate_url(url: str, allow_internal: bool) -> str | None:
    """校验 URL scheme / 主机安全性。返回错误消息或 None。"""
    url = (url or "").strip()
    if not url:
        return "URL 为空"
    try:
        parsed = urlparse(url)
    except ValueError:
        return "URL 无法解析"
    if parsed.scheme not in _ALLOWED_SCHEMES:
        return f"禁止的协议: {parsed.scheme or '(无)'}"
    host = (parsed.hostname or "").lower()
    if not host:
        return "URL 缺少主机"
    if not allow_internal:
        for blocked in _BLOCKED_NETWORKS:
            if host.startswith(blocked) or "localhost" in host or host.endswith(".local"):
                return f"禁止访问内网/回环地址: {host}"
    return None


class HTTPReadTool(BaseTool):
    """
    HTTP 只读工具：发送 GET 请求获取公开资源文本。

    入参（parameters）：
    - url: 必填，目标 URL
    - headers: 可选，请求头字典
    - timeout: 可选，覆盖默认超时（秒）
    """

    category: str = CATEGORY_NETWORK
    runtime_category: ToolCategory = ToolCategory.OBSERVE
    execution_mode: ExecutionMode = ExecutionMode.SYNC
    timeout: float = _DEFAULT_TIMEOUT
    permissions: frozenset[str] = frozenset({"observe:http"})
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "要读取的公开 URL"},
            "headers": {"type": "object", "description": "附加请求头（可选）"},
            "timeout": {"type": "number", "description": "请求超时（秒，可选）"},
        },
        "required": ["url"],
    }

    def __init__(self, settings: Settings | None = None):
        self._settings = settings or get_settings()
        self._allow_internal = bool(getattr(self._settings, "HTTP_ALLOW_INTERNAL", False))
        self._max_bytes = int(
            getattr(self._settings, "HTTP_MAX_RESPONSE_BYTES", _MAX_RESPONSE_BYTES)
        )

    @property
    def name(self) -> str:
        return "http.get"

    @property
    def description(self) -> str:
        return (
            "读取公开网页或 API 的内容（仅 GET）。输入 URL，返回响应体文本。"
            "禁止访问内网/回环地址。"
        )

    async def execute(
        self,
        input: ToolInput,
        context: ToolContext | None = None,
    ) -> ToolOutput:
        auth_error = self._authorize(context)
        if auth_error:
            return ToolOutput(success=False, error=auth_error)

        params = input.parameters or {}
        url = params.get("url") or input.query
        headers = params.get("headers") or {}
        if not isinstance(headers, dict):
            headers = {}
        if not url:
            return ToolOutput(success=False, error="缺少必填参数: url")

        url_error = _validate_url(str(url), self._allow_internal)
        if url_error:
            return ToolOutput(success=False, error=url_error)

        timeout = float(params.get("timeout") or self.timeout)
        if timeout <= 0:
            timeout = self.timeout

        try:
            import httpx

            async with httpx.AsyncClient(
                timeout=timeout,
                follow_redirects=True,
                limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
            ) as client:
                async with client.stream("GET", url, headers=headers) as resp:
                    chunks: list[bytes] = []
                    total = 0
                    async for chunk in resp.aiter_bytes():
                        total += len(chunk)
                        if total > self._max_bytes:
                            return ToolOutput(
                                success=False,
                                error=f"响应体超过大小限制（{self._max_bytes} 字节）",
                            )
                        chunks.append(chunk)
                    body = b"".join(chunks)

            if resp.status_code >= 400:
                return ToolOutput(
                    success=False,
                    error=f"HTTP {resp.status_code} {resp.reason_phrase}",
                )
            try:
                text = body.decode("utf-8")
            except UnicodeDecodeError:
                text = body.decode("latin-1", errors="replace")
            return ToolOutput(
                success=True,
                data={
                    "status_code": resp.status_code,
                    "content_type": resp.headers.get("content-type", ""),
                    "body": text[: self._max_bytes],
                    "bytes": total,
                },
            )
        except httpx.TimeoutException:
            raise ExternalServiceError(
                "请求超时", retryable=True, tool_name=self.name
            ) from None
        except httpx.HTTPError as e:  # 网络/连接层错误
            logger.warning("http.get 请求失败", error=str(e))
            raise ExternalServiceError(
                "远程服务暂不可达", retryable=True, tool_name=self.name
            ) from None
        except ValidationError:
            raise
        except Exception:
            raise ExternalServiceError(
                "读取失败", retryable=False, tool_name=self.name
            ) from None
