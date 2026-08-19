"""
Act — HTTP 外部 API 调用工具（http.request）。

支持 POST/PUT/PATCH/DELETE（副作用）。
设计：
- 提供 HttpActionClient 抽象接口，Agent Core 不绑定具体 HTTP 客户端/鉴权；
- 默认实现 HttpxActionClient 进行校验（仅 http/https、超时、大小上限）；
- 该工具标记 available=False（无内置外部 API 目标），由配置接入真实端点后启用；
- 副作用工具：默认不重试（幂等由调用方提供 idempotency-key）。
"""

from __future__ import annotations

from typing import Any, Protocol
from urllib.parse import urlparse

from app.config.logging import get_logger
from app.config.settings import Settings, get_settings
from app.tools.base import BaseTool, ToolInput, ToolOutput
from app.tools.schema import ExecutionMode, ToolCategory
from app.tools.security import CATEGORY_NETWORK, ToolContext

logger = get_logger(__name__)

_ALLOWED_SCHEMES = {"http", "https"}
_ALLOWED_METHODS = {"post", "put", "patch", "delete"}
_MAX_BODY_BYTES = 2 * 1024 * 1024


class HttpActionClient(Protocol):
    """外部 HTTP 动作客户端抽象（便于注入鉴权/重试/Mock）。"""

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        json_body: Any = None,
        timeout: float = 10.0,
    ) -> tuple[int, dict[str, Any]]:
        """执行请求，返回 (status_code, response_json)。"""
        ...


def _validate_target(method: str, url: str) -> str | None:
    method = (method or "").lower()
    if method not in _ALLOWED_METHODS:
        return f"禁止的方法: {method or '(空)'}（仅允许 {sorted(_ALLOWED_METHODS)}）"
    try:
        parsed = urlparse(url)
    except ValueError:
        return "URL 无法解析"
    if parsed.scheme not in _ALLOWED_SCHEMES:
        return f"禁止的协议: {parsed.scheme or '(无)'}"
    host = (parsed.hostname or "").lower()
    if not host:
        return "URL 缺少主机"
    blocked = ("127.", "10.", "192.168.", "169.254.")
    if any(host.startswith(b) for b in blocked) or "localhost" in host:
        return f"禁止访问内网/回环地址: {host}"
    return None


class HttpxActionClient:
    """基于 httpx 的默认实现。"""

    async def request(self, method: str, url: str, *, headers=None, json_body=None, timeout=10.0):
        import httpx

        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            resp = await client.request(
                method.upper(), url, headers=headers, json=json_body
            )
            try:
                data: dict[str, Any] = resp.json()
            except Exception:
                data = {"status_code": resp.status_code, "body": resp.text[:_MAX_BODY_BYTES]}
            return resp.status_code, data


class HTTPActionTool(BaseTool):
    """
    HTTP 动作工具（POST/PUT/PATCH/DELETE）。默认不可用，需配置真实端点后启用。

    TODO: 接入真实外部 API 目标后置 available=True，并支持按目标配置鉴权令牌/幂等键。
    """

    category: str = CATEGORY_NETWORK
    runtime_category: ToolCategory = ToolCategory.ACT
    execution_mode: ExecutionMode = ExecutionMode.SYNC
    timeout: float = 15.0
    permissions: frozenset[str] = frozenset({"act:http"})
    metadata: dict[str, Any] = {"side_effect": True, "risk": "high", "idempotent": False}
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "method": {"type": "string", "enum": ["post", "put", "patch", "delete"]},
            "url": {"type": "string"},
            "headers": {"type": "object"},
            "body": {"type": "object", "description": "JSON 请求体"},
        },
        "required": ["method", "url"],
    }

    def __init__(self, settings: Settings | None = None, client: HttpActionClient | None = None):
        self._settings = settings or get_settings()
        self._client = client or HttpxActionClient()
        # 默认不可用：Side-effect 工具需显式配置端点方可开放
        self.available = False

    @property
    def name(self) -> str:
        return "http.request"

    @property
    def description(self) -> str:
        return (
            "向外部 API 发起 POST/PUT/PATCH/DELETE 请求（副作用操作）。"
            "高风险，需审批。当前未配置目标端点，暂不可用。"
        )

    async def execute(self, input: ToolInput, context: ToolContext | None = None) -> ToolOutput:
        auth_error = self._authorize(context)
        if auth_error:
            return ToolOutput(success=False, error=auth_error)
        if not self.available:
            return ToolOutput(success=False, error="http.request 暂不可用（未配置目标端点）")

        params = input.parameters or {}
        method = str(params.get("method") or "").lower()
        url = str(params.get("url") or "")
        err = _validate_target(method, url)
        if err:
            return ToolOutput(success=False, error=err)
        headers = params.get("headers") or {}
        body = params.get("body")
        timeout = float(params.get("timeout") or self.timeout)
        try:
            status, data = await self._client.request(
                method, url, headers=headers if isinstance(headers, dict) else None,
                json_body=body, timeout=timeout,
            )
        except Exception as e:
            logger.warning("http.request 失败", error=str(e))
            return ToolOutput(success=False, error="外部服务调用失败")
        return ToolOutput(
            success=status < 400,
            data={"status_code": status, "response": data},
        )
