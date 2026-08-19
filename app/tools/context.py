"""
统一 Tool 执行上下文。

ToolExecutionContext 是每个 Tool 调用贯穿全管线的上下文，聚合了：

- 身份：复用既有 ToolContext（user_id/tenant_id/role/trace_id）；
- 链路：request_id / agent_id / session_id / execution_id；
- 权限：granted_permissions（该执行主体被授予的权限字符串集合）；
- 控制：cancellation_event（协作式取消）、metadata；
- 日志：logger（按执行域命名，便于审计归因）。

用途：tracing / audit / cancellation / timeout / multi-user 隔离 / observability。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from app.config.logging import get_logger
from app.tools.security import ROLE_ADMIN, ToolContext


@dataclass
class ToolExecutionContext:
    """一次工具调用的统一执行上下文。"""

    tool_context: ToolContext = field(default_factory=ToolContext)
    request_id: str = ""
    agent_id: str = ""
    session_id: str = ""
    execution_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    # None 表示"未显式限定"，交给既有角色×类别矩阵判定；
    # 一旦提供，则按 granted_allows() 与工具所需权限做交集校验。
    granted_permissions: set[str] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    # 协作式取消：置位后 Tool 在执行前/执行中检查并尽快终止。
    _cancelled: bool = False

    # ---- 身份快捷属性 ----
    @property
    def user_id(self) -> str:
        return self.tool_context.user_id

    @property
    def tenant_id(self) -> str:
        return self.tool_context.tenant_id

    @property
    def role(self) -> str:
        return self.tool_context.role

    @classmethod
    def from_tool_context(
        cls,
        tool_context: ToolContext | None,
        *,
        request_id: str = "",
        agent_id: str = "",
        session_id: str = "",
        granted_permissions: set[str] | None = None,
        **kwargs: Any,
    ) -> "ToolExecutionContext":
        return cls(
            tool_context=tool_context or ToolContext(),
            request_id=request_id,
            agent_id=agent_id,
            session_id=session_id,
            granted_permissions=granted_permissions,
            **kwargs,
        )

    def logger(self, tool_name: str = ""):
        """返回归因到执行域的日志器。"""
        return get_logger(f"toolcx:{tool_name or 'tool'}")

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    def cancel(self) -> None:
        """请求取消：协作式（不强制打断，具体 Tool 在执行点检查）。"""
        self._cancelled = True

    def grant(self, permissions: set[str] | None) -> None:
        """注入/覆盖该执行主体被授予的权限集合。"""
        self.granted_permissions = permissions


def default_execution_context(role: str = ROLE_ADMIN) -> ToolExecutionContext:
    """构造默认执行上下文（内部调用，等价于历史 admin 语义）。"""
    return ToolExecutionContext(tool_context=ToolContext(role=role))
