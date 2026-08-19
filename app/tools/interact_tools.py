"""
Interact — 用户/环境交互工具集（user.message / user.ask / user.approval）。

核心设计（需求 §9）：把 Tool execution 与 User interaction 分离。具体交互投递
（UI/即时通讯/审批闸门）通过 InteractTransport 抽象注入，Agent Core 不绑定任一通道。

- user.message：向用户发送一条通知（可投递到 UI/IM 等，当前默认内存 sink）。
- user.ask：向用户请求输入；Worker 环境无实时通道时默认返回 APPROVAL_REQUIRED，
  由上层（Agent 审批/交互层）接管。
- user.approval：请求用户对高风险操作确认；交由审批闸门判定。

Transports 注入原则：具体交互走 transport，而非把 UI/IM 逻辑写进 Agent Core。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.config.logging import get_logger
from app.tools.base import BaseTool, ToolInput, ToolOutput
from app.tools.schema import ExecutionMode, ToolCategory
from app.tools.security import CATEGORY_SYSTEM, ToolContext

logger = get_logger(__name__)


@dataclass
class MessageRecord:
    to: str
    text: str
    kind: str = "message"  # message | question | approval
    created_at: float = field(default_factory=time.time)


class InteractTransport(Protocol):
    """交互投递抽象：把消息/提问/审批请求递给用户，取回用户响应。"""

    async def deliver(self, user_id: str, kind: str, payload: dict[str, Any]) -> str:
        """
        投递交互内容并返回用户响应。

        kind: message|question|approval
        payload: 数据（text/args 等）
        """
        ...


class InMemoryTransport:
    """内存实现：仅记录投递消息（不真实等待用户），供流程验证。"""

    records: list[MessageRecord] = []

    async def deliver(self, user_id, kind, payload):
        self.records.append(
            MessageRecord(to=user_id, text=str(payload.get("text", "")), kind=kind)
        )
        # 模拟：消息类直接确认；（真实提问/审批应由接入的实时通道接管）
        return "ack"


class _InteractBase(BaseTool):
    category: str = CATEGORY_SYSTEM
    runtime_category: ToolCategory = ToolCategory.INTERACT
    execution_mode: ExecutionMode = ExecutionMode.SYNC
    timeout: float = 5.0

    def __init__(self, transport: InteractTransport | None = None):
        # 默认内存 transport（仅记录，不真实等待用户）。
        self._transport: InteractTransport | None = transport


class UserMessageTool(_InteractBase):
    permissions: frozenset[str] = frozenset({"interact:message"})
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    }

    @property
    def name(self) -> str:
        return "user.message"

    @property
    def description(self) -> str:
        return "向用户发送一条消息/通知。"

    async def execute(self, input: ToolInput, context: ToolContext | None = None) -> ToolOutput:
        auth_error = self._authorize(context)
        if auth_error:
            return ToolOutput(success=False, error=auth_error)
        params = input.parameters or {}
        text = str(params.get("text") or input.query).strip()
        if not text:
            return ToolOutput(success=False, error="缺少必填参数: text")
        transport = self._transport or InMemoryTransport()
        user_id = context.user_id if context else "anonymous"
        await transport.deliver(user_id, "message", {"text": text})
        return ToolOutput(success=True, data={"delivered": True})


class UserAskTool(_InteractBase):
    permissions: frozenset[str] = frozenset({"interact:ask"})
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {"question": {"type": "string"}},
        "required": ["question"],
    }

    @property
    def name(self) -> str:
        return "user.ask"

    @property
    def description(self) -> str:
        return "向用户提问并等待回答（Worker 无实时通道时返回需审批/接管）。"

    async def execute(self, input: ToolInput, context: ToolContext | None = None) -> ToolOutput:
        auth_error = self._authorize(context)
        if auth_error:
            return ToolOutput(success=False, error=auth_error)
        params = input.parameters or {}
        q = str(params.get("question") or input.query).strip()
        if not q:
            return ToolOutput(success=False, error="缺少必填参数: question")
        if self._transport is None:
            # 无实时交互通道：交由上层 Agent 交互/审批层接管
            return ToolOutput(
                success=False,
                error="需要用户输入，但在当前执行通道无实时会话（APPROVAL_REQUIRED）",
            )
        user_id = context.user_id if context else "anonymous"
        reply = await self._transport.deliver(user_id, "question", {"text": q})
        return ToolOutput(success=True, data={"answer": reply})


class UserApprovalTool(_InteractBase):
    permissions: frozenset[str] = frozenset({"interact:approval"})
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "description": "需确认的操作描述"},
            "args": {"type": "object", "description": "操作参数"},
        },
        "required": ["action"],
    }

    @property
    def name(self) -> str:
        return "user.approval"

    @property
    def description(self) -> str:
        return "请求用户确认高风险操作（需配置审批闸门/实时通道）。"

    async def execute(self, input: ToolInput, context: ToolContext | None = None) -> ToolOutput:
        auth_error = self._authorize(context)
        if auth_error:
            return ToolOutput(success=False, error=auth_error)
        params = input.parameters or {}
        action = str(params.get("action") or input.query).strip()
        if not action:
            return ToolOutput(success=False, error="缺少必填参数: action")
        if self._transport is None:
            return ToolOutput(
                success=False,
                error="需要人工审批，但未配置审批通道（APPROVAL_REQUIRED）",
            )
        user_id = context.user_id if context else "anonymous"
        reply = await self._transport.deliver(
            user_id, "approval", {"text": action, "args": params.get("args") or {}}
        )
        return ToolOutput(success=True, data={"decision": reply})
