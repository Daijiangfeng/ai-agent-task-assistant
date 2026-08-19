"""
Human-in-the-loop 工具审批闸门。

Agent 准备执行高风险动作（副作用工具：sql_query / file_processing / web_search 等）时，
ApprovalGate 决定执行路径：

- AutoApprovalGate（默认）：自动放行，保持原有"审批钩子默认放行"的语义；
- HumanApprovalGate：需要人工确认时通过 LangGraph interrupt 暂停整个 Workflow，
  待用户批准 / 拒绝 / 修改参数后，由 AgentService 以 Command(resume=...) 恢复执行。

恢复时 interrupt() 返回用户决策：
    {"decision": "approved", "args": <修改后的参数（可选）>}
    {"decision": "rejected", "reason": "拒绝原因"}
未确认（如未携带决策恢复）视为 cancelled：工具不执行，回填说明给 LLM。
"""

from __future__ import annotations

import time
import uuid
from abc import ABC, abstractmethod
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from langgraph.types import interrupt

from app.config.logging import get_logger

logger = get_logger(__name__)

APPROVAL_REQUEST_KIND = "tool_approval"


@dataclass(frozen=True)
class ApprovalOutcome:
    """审批闸门决策结果。"""

    decision: str  # "approved" | "rejected" | "cancelled"
    args: dict[str, Any] | None = None
    reason: str | None = None


@dataclass
class ApprovalRequestPayload:
    """通过 interrupt 传递给 AgentService 的审批请求载荷。"""

    approval_id: str
    task_id: str
    tool_name: str
    args: dict[str, Any]
    reason: str
    requested_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": APPROVAL_REQUEST_KIND,
            "approval_id": self.approval_id,
            "task_id": self.task_id,
            "tool_name": self.tool_name,
            "args": self.args,
            "reason": self.reason,
            "requested_at": self.requested_at,
        }


class ApprovalGate(ABC):
    """工具执行审批闸门抽象接口。"""

    @abstractmethod
    async def request(
        self,
        tool_name: str,
        args: dict[str, Any],
        *,
        task_id: str = "",
    ) -> ApprovalOutcome:
        """
        请求执行某次工具调用。

        Args:
            tool_name: 工具名称。
            args: 工具调用参数。
            task_id: 所属任务 ID（Trace 归因）。

        Returns:
            决策结果：approved（可携带修改后参数）/ rejected / cancelled。
        """
        ...


class AutoApprovalGate(ApprovalGate):
    """
    自动放行闸门（默认行为）。

    与原有"审批钩子缺省放行"语义一致：所有工具调用直接执行。
    由上层决定是否注入 HumanApprovalGate。
    """

    async def request(
        self,
        tool_name: str,
        args: dict[str, Any],
        *,
        task_id: str = "",
    ) -> ApprovalOutcome:
        return ApprovalOutcome(decision="approved", args=args)


class HumanApprovalGate(ApprovalGate):
    """
    人工审批闸门：高风险工具调用前暂停，等待用户决策。

    审批判定（三级风险模型，可配置，不硬编码）：
    - 未显式指定 require_approval_tools 时，按工具风险分级判定：
      L0（只读，如 web_search/calculator）默认 AUTO 不审批；
      L1（有业务影响）按 TOOL_APPROVAL_LEVEL 策略；
      L2（高风险不可逆，如 email.send/删除数据）默认 HITL；
    - 显式指定 require_approval_tools 时，仅这些工具需审批（保持旧语义）；
    - TOOL_APPROVAL_OVERRIDE_TOOLS 强制要求审批（优先级最高）。

    在需要审批的工具上调用 interrupt()：
    - 首次调用抛出 GraphInterrupt，LangGraph 保存检查点并暂停 Workflow，
      AgentService 捕获 __interrupt__ 事件后持久化审批请求并置任务为
      awaiting_approval；
    - 用户决策后以 Command(resume=决策) 恢复，interrupt() 返回决策值。
    """

    def __init__(
        self,
        require_approval_tools: Iterable[str] | None = None,
        reason_template: str | None = None,
        approval_level: str | None = None,
    ):
        # None 表示使用风险分级判定；显式集合表示仅这些工具需审批（旧语义）。
        self._tools: set[str] | None = (
            set(require_approval_tools) if require_approval_tools is not None else None
        )
        self._approval_level = approval_level
        self._reason_template = reason_template or "Agent 请求执行高风险操作: {tool}"

    def _needs_approval(self, tool_name: str) -> bool:
        """判断某工具是否需要人工审批（风险分级 + 显式覆盖）。"""
        if self._tools is not None:
            return tool_name in self._tools
        from app.config.settings import get_settings
        from app.tools.risk import requires_approval

        settings = get_settings()
        if tool_name in settings.TOOL_APPROVAL_OVERRIDE_TOOLS:
            return True
        return requires_approval(tool_name, self._approval_level)

    async def request(
        self,
        tool_name: str,
        args: dict[str, Any],
        *,
        task_id: str = "",
    ) -> ApprovalOutcome:
        if not self._needs_approval(tool_name):
            return ApprovalOutcome(decision="approved", args=args)

        payload = ApprovalRequestPayload(
            approval_id=uuid.uuid4().hex,
            task_id=task_id,
            tool_name=tool_name,
            args=dict(args or {}),
            reason=self._reason_template.format(tool=tool_name),
        )
        logger.info(
            "HumanApprovalGate: 请求人工审批，暂停执行",
            approval_id=payload.approval_id,
            tool=tool_name,
            task_id=task_id,
        )

        decision = interrupt(payload.to_dict())

        # 未携带决策恢复（如误触发续跑）：按取消处理，不执行工具
        if decision is None:
            logger.warning(
                "HumanApprovalGate: 审批未确认，工具调用取消",
                approval_id=payload.approval_id,
                tool=tool_name,
            )
            return ApprovalOutcome(decision="cancelled", reason="审批未确认")

        decision_type = str(decision.get("decision", "")).lower()
        if decision_type == "approved":
            modified = decision.get("args")
            logger.info(
                "HumanApprovalGate: 已批准",
                approval_id=payload.approval_id,
                tool=tool_name,
                args_modified=bool(modified and modified != payload.args),
            )
            return ApprovalOutcome(
                decision="approved", args=modified or payload.args
            )
        return ApprovalOutcome(
            decision="rejected",
            reason=str(decision.get("reason") or "用户拒绝了该操作"),
        )
