"""
统一 Agent 上下文（AgentContext）。

在 LangGraph AgentState 之上提供轻量读写视图，聚合多 Agent 协作全链路
（Supervisor -> SubAgents -> Reviewer）所需的全部上下文信息：

    original_user_query     用户原始输入（含多轮对话全文）
    conversation_history    对话历史
    extracted_requirements  已提取的结构化用户参数
    missing_requirements    缺失的必要参数
    intermediate_results    中间结果
    tool_results            工具调用结果
    subagent_results        子 Agent 产出

设计原则：
- 不重复创建平行的新 State：AgentContext 只是 AgentState 的视图，
  字段与 AgentState 一一对应，通过 from_state / to_state_dict 互转；
- 上下文必须结构化透传，禁止用字符串拼接"伪造上下文"；
- 列表字段使用 LangGraph add reducer 累加，避免串行/并行结果相互覆盖。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.agent.state import AgentState


@dataclass
class AgentContext:
    """多 Agent 协作的统一上下文（AgentState 的读写视图）。"""

    original_user_query: str = ""
    conversation_history: list[dict[str, Any]] = field(default_factory=list)
    extracted_requirements: dict[str, Any] = field(default_factory=dict)
    missing_requirements: list[str] = field(default_factory=list)
    intermediate_results: list[dict[str, Any]] = field(default_factory=list)
    tool_results: list[dict[str, Any]] = field(default_factory=list)
    subagent_results: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_state(cls, state: AgentState | dict[str, Any]) -> "AgentContext":
        """从 AgentState（或任意 dict）构造上下文视图。"""
        return cls(
            original_user_query=str(
                state.get("original_user_query") or state.get("goal") or ""
            ),
            conversation_history=list(state.get("conversation_history") or []),
            extracted_requirements=dict(state.get("extracted_requirements") or {}),
            missing_requirements=list(state.get("missing_requirements") or []),
            intermediate_results=list(state.get("intermediate_results") or []),
            tool_results=list(state.get("tool_results") or []),
            subagent_results=list(
                state.get("subagent_results") or state.get("agent_results") or []
            ),
        )

    def to_state_dict(self) -> dict[str, Any]:
        """返回可合并进 AgentState 的完整状态更新（用于初始状态构造）。"""
        return {
            "original_user_query": self.original_user_query,
            "conversation_history": list(self.conversation_history),
            "extracted_requirements": dict(self.extracted_requirements),
            "missing_requirements": list(self.missing_requirements),
            "intermediate_results": list(self.intermediate_results),
            "tool_results": list(self.tool_results),
            "subagent_results": list(self.subagent_results),
        }

    @property
    def conversation_text(self) -> str:
        """对话历史拼接为纯文本（供需求提取/参数完整性检查）。"""
        return "\n".join(
            f"[{item.get('role', 'user')}] {item.get('content', '')}"
            for item in self.conversation_history
            if item.get("content")
        )

    def to_prompt_context(self) -> str:
        """格式化为注入 Prompt 的结构化上下文文本。"""
        parts: list[str] = []
        if self.original_user_query:
            parts.append(f"原始用户输入：{self.original_user_query}")
        if self.extracted_requirements:
            reqs = "，".join(
                f"{k}={v}" for k, v in self.extracted_requirements.items()
            )
            parts.append(f"已提取的用户参数：{reqs}")
        if self.missing_requirements:
            parts.append(f"缺失的必要参数：{', '.join(self.missing_requirements)}")
        if self.conversation_history:
            parts.append(f"对话历史：\n{self.conversation_text()}")
        if self.tool_results:
            tool_lines = "\n".join(
                f"[{item.get('tool', '?')}] {item.get('result', '')}"
                for item in self.tool_results
                if item.get("result")
            )
            if tool_lines:
                parts.append(f"工具调用结果：\n{tool_lines}")
        if self.subagent_results:
            sub_lines = "\n".join(
                f"[{item.get('agent_name') or item.get('role', '?')}] "
                f"{item.get('result') or item.get('error') or ''}"
                for item in self.subagent_results
                if item.get("result") or item.get("error")
            )
            if sub_lines:
                parts.append(f"子 Agent 产出：\n{sub_lines}")
        return "\n\n".join(parts)
