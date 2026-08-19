"""
Act — 邮件发送工具（email.send）。

设计：
- 提供 EmailProvider 抽象接口，Agent Core 不绑定具体邮件服务商；
- InMemoryEmailProvider 为测试/演示用（仅记录到内存，不真实发送），
  用于验证链路（审批 -> 执行 -> 审计）而不产生副作用；
- 生产接入需实现并注入真实 EmailProvider（SMTP/邮件 API）。
- 副作用工具：不自动重试（幂等由上层 idempotency-key 保证）。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.config.logging import get_logger
from app.tools.base import BaseTool, ToolInput, ToolOutput
from app.tools.errors import ValidationError
from app.tools.schema import ExecutionMode, ToolCategory
from app.tools.security import CATEGORY_NETWORK, ToolContext

logger = get_logger(__name__)


@dataclass
class SentEmail:
    to: str
    subject: str
    body: str
    cc: list[str] = field(default_factory=list)
    sent_at: float = field(default_factory=time.time)


class EmailProvider(Protocol):
    """邮件服务提供商抽象。"""

    async def send(
        self,
        to: str,
        subject: str,
        body: str,
        *,
        cc: list[str] | None = None,
    ) -> dict[str, Any]:
        """发送邮件，返回服务商消息 ID 等。"""
        ...


class InMemoryEmailProvider:
    """内存实现：不真实发送，记录已发邮件供测试/演示。"""

    sent: list[SentEmail] = []

    async def send(self, to, subject, body, *, cc=None):
        self.sent.append(SentEmail(to=to, subject=subject, body=body, cc=cc or []))
        return {"message_id": f"mem-{len(self.sent)}", "sent": True}


class EmailTool(BaseTool):
    """
    邮件发送工具。

    入参（parameters）：
    - to: 必填，收件人（可逗号分隔多个）
    - subject: 必填，主题
    - body: 必填，正文
    - cc: 可选，抄送列表

    TODO: 接入真实 SMTP/邮件 API 后替换 EmailProvider 实现。
    """

    category: str = CATEGORY_NETWORK
    runtime_category: ToolCategory = ToolCategory.ACT
    execution_mode: ExecutionMode = ExecutionMode.SYNC
    timeout: float = 15.0
    permissions: frozenset[str] = frozenset({"act:email"})
    metadata: dict[str, Any] = {"side_effect": True, "risk": "high", "idempotent": False}
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "to": {"type": "string", "description": "收件人邮箱"},
            "subject": {"type": "string"},
            "body": {"type": "string"},
        },
        "required": ["to", "subject", "body"],
    }

    def __init__(self, provider: EmailProvider | None = None):
        # 默认使用内存 Provider（仅验证链路，不真实发送）。可注入真实 Provider。
        self._provider: EmailProvider = provider or InMemoryEmailProvider()

    @property
    def name(self) -> str:
        return "email.send"

    @property
    def description(self) -> str:
        return (
            "发送邮件（高风险副作用操作，执行前需用户审批）。"
            "当前使用内存通道，仅用于流程验证，不真实外发。"
        )

    async def execute(self, input: ToolInput, context: ToolContext | None = None) -> ToolOutput:
        auth_error = self._authorize(context)
        if auth_error:
            return ToolOutput(success=False, error=auth_error)

        params = input.parameters or {}
        to = str(params.get("to") or "").strip()
        subject = str(params.get("subject") or "").strip()
        body = str(params.get("body") or "")
        cc = params.get("cc") or []
        if not to:
            raise ValidationError("缺少必填参数: to", tool_name=self.name)
        if not subject:
            raise ValidationError("缺少必填参数: subject", tool_name=self.name)

        try:
            result = await self._provider.send(
                to, subject, body, cc=cc if isinstance(cc, list) else None
            )
        except Exception as e:
            logger.warning("email.send 失败", error=str(e))
            return ToolOutput(success=False, error="邮件发送失败")
        return ToolOutput(success=True, data=result)
