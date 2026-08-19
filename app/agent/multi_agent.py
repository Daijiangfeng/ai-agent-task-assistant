"""
多 Agent 协作（Supervisor 模式）节点。

升级单 Agent 串行链路（Planner -> Executor -> Reflection -> Replanner）：
    User -> Supervisor -> Research/Data/Coding/Writing/Review 子 Agent -> Reviewer -> 最终结果

Supervisor 判断任务是否需要跨领域协作：
- multi_agent：将目标拆分为若干子 Agent 目标（research/data/coding/writing/review），
  子 Agent 按角色绑定受限工具集（角色工具范围控制），顺序执行后由 Reviewer 合成最终结果；
- single：回退原有 Planner -> Executor -> Reflection 单 Agent 流程。

角色工具范围控制：每个角色仅绑定其职责相关的工具类别（如 research 仅
network+rag），降低误用风险；工具调用链复用 executor_node.run_tool_calls_loop
（含执行边界与人工审批闸门）。
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import AIMessage
from langchain_core.output_parsers import JsonOutputParser

from app.agent.context import AgentContext
from app.agent.executor_node import (
    _is_interrupt,
    extract_text_content,
    run_tool_calls_loop,
)
from app.agent.requirements import extract_requirements, merge_requirements
from app.agent.state import AgentState
from app.config.logging import get_logger
from app.config.settings import get_settings
from app.llm.base import BaseLLMProvider
from app.llm.budget import BudgetExceededError, budgeted_ainvoke
from app.prompts.manager import PromptManager
from app.tools.registry import ToolRegistry
from app.tools.security import (
    CATEGORY_FILE,
    CATEGORY_NETWORK,
    CATEGORY_SQL,
    CATEGORY_SYSTEM,
    ToolContext,
)
from app.tracing.recorder import AgentStepEvent, get_trace_recorder

logger = get_logger(__name__)

MODE_MULTI_AGENT = "multi_agent"
MODE_SINGLE = "single"

DEFAULT_AGENT_ROLE = "general"


def _extract_external_knowledge(context: str) -> str:
    """从 context 中提取 <external_knowledge> 标记的历史记忆块。"""
    start = context.find("<external_knowledge>")
    end = context.find("</external_knowledge>")
    if start != -1 and end != -1 and end > start:
        return context[start + len("<external_knowledge>") : end].strip()
    return "无"


async def _ainvoke_with_timeout(runnable, input, timeout: float):
    """
    在预算上下文内调用 LLM，并施加超时保护（防止外部模型挂起导致系统无限等待）。

    超时取消仅作用于 LLM 调用本身；LangGraph interrupt（工具审批暂停）异常
    会正常透传，不受超时影响。
    """
    return await asyncio.wait_for(
        budgeted_ainvoke(runnable, input), timeout=timeout
    )


@dataclass(frozen=True)
class AgentRole:
    """子 Agent 角色定义：名称、职责说明与可用的工具类别集合。"""

    role: str
    name: str
    description: str
    tool_categories: frozenset[str]


# 内置子 Agent 角色注册表（角色 -> 定义）
AGENT_ROLES: dict[str, AgentRole] = {
    "research": AgentRole(
        role="research",
        name="Research Agent",
        description="负责搜索资料并汇总事实性信息",
        tool_categories=frozenset({CATEGORY_NETWORK}),
    ),
    "data": AgentRole(
        role="data",
        name="Data Agent",
        description="负责整理数据、执行统计与数值分析",
        tool_categories=frozenset({CATEGORY_SQL, CATEGORY_SYSTEM}),
    ),
    "coding": AgentRole(
        role="coding",
        name="Coding Agent",
        description="负责代码相关任务：读取、分析与生成代码",
        tool_categories=frozenset({CATEGORY_FILE, CATEGORY_SYSTEM}),
    ),
    "writing": AgentRole(
        role="writing",
        name="Writing Agent",
        description="负责撰写文档、报告与摘要等文本产出",
        tool_categories=frozenset({CATEGORY_SYSTEM}),
    ),
    "review": AgentRole(
        role="review",
        name="Review Agent",
        description="负责检查事实一致性、质量与完整性",
        tool_categories=frozenset({CATEGORY_SYSTEM}),
    ),
}

# 未知角色回退：允许全部工具类别
GENERIC_ROLE = AgentRole(
    role=DEFAULT_AGENT_ROLE,
    name="General Agent",
    description="通用 Agent，负责完成分配的目标",
    tool_categories=frozenset(
        {CATEGORY_SYSTEM, CATEGORY_SQL, CATEGORY_FILE, CATEGORY_NETWORK}
    ),
)

ALL_ROLES = list(AGENT_ROLES) + [DEFAULT_AGENT_ROLE]


class SupervisorNode:
    """
    Supervisor Agent 节点。

    判断执行模式并分配子 Agent 目标：
    - multi_agent：产出 agent_assignments（[{role, objective}]）；
    - single：回退单 Agent 流程（Planner 处理）。
    """

    def __init__(self, llm_provider: BaseLLMProvider, prompt_manager: PromptManager):
        self.llm = llm_provider.get_chat_model()
        self.prompt_manager = prompt_manager

    async def run(self, state: AgentState) -> dict[str, Any]:
        logger.info("Supervisor: 开始编排", goal=state["goal"])
        prompt = self.prompt_manager.get_supervisor_prompt()
        parser = JsonOutputParser()
        chain = prompt | self.llm | parser

        # 统一上下文：原始需求 + 对话历史 + 已提取参数（确定性规则，非 LLM 猜测）
        ctx = AgentContext.from_state(state)
        extracted = merge_requirements(
            state.get("extracted_requirements"),
            extract_requirements(ctx.original_user_query),
            extract_requirements(ctx.conversation_text),
        )
        t0 = time.perf_counter()

        # 从 context 提取 <external_knowledge> 历史记忆块，注入 prompt（无则占位）
        recalled_memory = _extract_external_knowledge(state.get("context") or "")
        try:
            decision = await budgeted_ainvoke(
                chain,
                {
                    "goal": state["goal"],
                    "context": state.get("context") or "无",
                    "recalled_memory": recalled_memory,
                },
            )
        except BudgetExceededError:
            raise
        except Exception as e:
            # 编排失败（含解析失败）：回退单 Agent 流程，不阻断任务
            logger.warning("Supervisor: 编排失败，回退单 Agent", error=str(e))
            return {
                "execution_mode": MODE_SINGLE,
                "extracted_requirements": extracted,
            }

        mode = str(decision.get("mode", MODE_SINGLE)).strip().lower()
        assignments = decision.get("agents") or []
        if mode == MODE_MULTI_AGENT and assignments:
            clean: list[dict[str, Any]] = []
            for item in assignments:
                role = str(item.get("role", DEFAULT_AGENT_ROLE)).strip().lower()
                if role not in AGENT_ROLES:
                    role = DEFAULT_AGENT_ROLE
                clean.append(
                    {
                        "role": role,
                        "objective": str(item.get("objective", "")).strip(),
                    }
                )
            clean = [a for a in clean if a["objective"]]
            if clean:
                logger.info(
                    "Supervisor: 多 Agent 协作",
                    agents=[a["role"] for a in clean],
                    reasoning=decision.get("reasoning"),
                )
                get_trace_recorder().record_agent_step(
                    state["task_id"],
                    AgentStepEvent(
                        agent_name="Supervisor",
                        input=ctx.original_user_query,
                        context_snapshot=ctx.to_state_dict(),
                        extracted_requirements=extracted,
                        missing_requirements=list(ctx.missing_requirements),
                        output=json.dumps(clean, ensure_ascii=False),
                        latency_ms=(time.perf_counter() - t0) * 1000,
                    ),
                )
                return {
                    "execution_mode": MODE_MULTI_AGENT,
                    "agent_assignments": clean,
                    "extracted_requirements": extracted,
                }

        logger.info("Supervisor: 单 Agent 流程")
        return {
            "execution_mode": MODE_SINGLE,
            "extracted_requirements": extracted,
        }


class SubAgentsNode:
    """
    子 Agent 执行节点。

    按 Supervisor 分配的顺序依次执行每个子 Agent：
    - 按角色绑定受限工具集（角色工具范围控制）；
    - 工具调用链复用 run_tool_calls_loop（执行边界 + 人工审批闸门）；
    - 前序 Agent 产出作为后续 Agent 的上下文输入。
    """

    def __init__(
        self,
        llm_provider: BaseLLMProvider,
        prompt_manager: PromptManager,
        approval_gate=None,
    ):
        self.llm = llm_provider.get_chat_model()
        self.prompt_manager = prompt_manager
        self._approval_gate = approval_gate

    async def run(self, state: AgentState) -> dict[str, Any]:
        assignments = state.get("agent_assignments") or []
        if not assignments:
            return {"agent_results": [], "errors": ["SubAgents: 无 Agent 分配"]}

        # 统一上下文透传：每个子 Agent 都能访问原始需求与已提取参数
        ctx = AgentContext.from_state(state)
        # 防无限循环：限制单任务子 Agent 最大执行轮数
        max_steps = get_settings().MAX_AGENT_STEPS
        if len(assignments) > max_steps:
            logger.warning(
                "SubAgents: 子 Agent 数量超过上限，截断执行",
                count=len(assignments),
                max_steps=max_steps,
                task_id=state["task_id"],
            )
            assignments = assignments[:max_steps]

        logger.info(
            "SubAgents: 开始执行",
            count=len(assignments),
            task_id=state["task_id"],
        )
        tool_context = ToolContext.from_dict(state.get("tool_context"))
        results: list[dict[str, Any]] = []
        previous_results: list[dict[str, Any]] = []

        for assignment in assignments:
            result = await self._run_one(
                assignment, previous_results, tool_context, state["task_id"], ctx
            )
            results.append(result)
            previous_results.append(result)

        logger.info(
            "SubAgents: 全部执行完成",
            count=len(results),
            task_id=state["task_id"],
        )
        return {
            "agent_results": results,
            "subagent_results": results,
            "intermediate_results": results,
            "messages": [
                AIMessage(
                    content=f"Multi-agent execution completed: {len(results)} agents"
                )
            ],
            "errors": [],
        }

    async def _run_one(
        self,
        assignment: dict[str, Any],
        previous_results: list[dict[str, Any]],
        tool_context: ToolContext,
        task_id: str,
        ctx: AgentContext,
    ) -> dict[str, Any]:
        role = str(assignment.get("role", DEFAULT_AGENT_ROLE))
        objective = str(assignment.get("objective", ""))
        agent = AGENT_ROLES.get(role, GENERIC_ROLE)

        tools = ToolRegistry.get_langchain_tools_by_categories(
            agent.tool_categories
        )
        llm_with_tools = self.llm.bind_tools(tools) if tools else self.llm

        previous_text = "\n".join(
            f"[{r.get('role', '?')}] {r.get('objective', '')} -> {r.get('result', '')}"
            for r in previous_results
            if r.get("result")
        ) or "无"

        prompt = self.prompt_manager.get_sub_agent_prompt()
        messages = prompt.format_messages(
            role_name=agent.name,
            role_description=agent.description,
            objective=objective,
            previous_results=previous_text,
            original_user_query=ctx.original_user_query or "无",
            extracted_requirements=json.dumps(
                ctx.extracted_requirements, ensure_ascii=False
            )
            or "{}",
            missing_requirements="、".join(ctx.missing_requirements) or "无",
        )

        t0 = time.perf_counter()
        collected_tools: list[dict[str, Any]] = []
        timeout = get_settings().SUB_AGENT_TIMEOUT_SECONDS

        async def _run_agent() -> str:
            response = await _ainvoke_with_timeout(llm_with_tools, messages, timeout)
            if getattr(response, "tool_calls", None):
                return await run_tool_calls_loop(
                    llm_with_tools,
                    messages,
                    response,
                    tools,
                    approval_gate=self._approval_gate,
                    tool_context=tool_context,
                    task_id=task_id,
                    extracted_requirements=ctx.extracted_requirements,
                    conversation=ctx.conversation_text,
                    tool_results=collected_tools,
                )
            return extract_text_content(response.content)

        try:
            result_content = await _run_agent()
            status = "completed"
            error = None
        except asyncio.TimeoutError:
            logger.error(
                "SubAgents: 子 Agent 执行超时",
                role=role,
                timeout=timeout,
                task_id=task_id,
            )
            result_content = None
            status = "failed"
            error = f"子 Agent 执行超时（>{timeout}s）"
        except BudgetExceededError:
            raise
        except Exception as e:
            if _is_interrupt(e):
                raise  # 审批暂停信号透传，由 AgentService 持久化审批请求
            logger.error(
                "SubAgents: 子 Agent 执行失败",
                role=role,
                error=str(e),
                task_id=task_id,
            )
            result_content = None
            status = "failed"
            error = str(e)

        latency_ms = (time.perf_counter() - t0) * 1000
        logger.info(
            "SubAgents: 子 Agent 完成",
            role=role,
            status=status,
            latency_ms=round(latency_ms, 1),
            task_id=task_id,
        )
        get_trace_recorder().record_agent_step(
            task_id,
            AgentStepEvent(
                agent_name=agent.name,
                parent_agent="Supervisor",
                input=objective,
                context_snapshot=ctx.to_state_dict(),
                extracted_requirements=dict(ctx.extracted_requirements),
                missing_requirements=list(ctx.missing_requirements),
                output=result_content,
                error=error,
                latency_ms=latency_ms,
            ),
        )
        return {
            "role": role,
            "agent_name": agent.name,
            "objective": objective,
            "result": result_content,
            "status": status,
            "error": error,
            "latency_ms": round(latency_ms, 1),
            "tool_results": collected_tools,
        }


class ReviewerNode:
    """
    Reviewer 评审节点。

    审阅全部子 Agent 产出，合成最终交付结果（事实与质量检查）。
    """

    def __init__(self, llm_provider: BaseLLMProvider, prompt_manager: PromptManager):
        self.llm = llm_provider.get_chat_model()
        self.prompt_manager = prompt_manager

    async def run(self, state: AgentState) -> dict[str, Any]:
        agent_results = state.get("agent_results") or []
        ctx = AgentContext.from_state(state)
        prompt = self.prompt_manager.get_reviewer_prompt()
        messages = prompt.format_messages(
            goal=state["goal"],
            original_user_query=ctx.original_user_query or "无",
            extracted_requirements=json.dumps(
                ctx.extracted_requirements, ensure_ascii=False
            )
            or "{}",
            agent_results=json.dumps(agent_results, ensure_ascii=False),
            tool_results=json.dumps(ctx.tool_results, ensure_ascii=False) or "[]",
        )
        t0 = time.perf_counter()
        timeout = get_settings().SUB_AGENT_TIMEOUT_SECONDS
        try:
            response = await _ainvoke_with_timeout(self.llm, messages, timeout)
            final_result = extract_text_content(response.content).strip()
            error = None
        except asyncio.TimeoutError:
            logger.error(
                "Reviewer: 评审超时",
                timeout=timeout,
                task_id=state["task_id"],
            )
            parts = [
                f"[{r.get('agent_name') or r.get('role', '?')}] "
                f"{r.get('result') or r.get('error') or '（无产出）'}"
                for r in agent_results
                if r.get("result") or r.get("error")
            ]
            final_result = (
                "多 Agent 执行完成，但最终评审超时，以下为各 Agent 产出汇总（参考）：\n"
                + ("\n".join(parts) if parts else "（各 Agent 均无产出）")
                + f"\n评审超时: 超过 {timeout}s"
            )
            error = f"评审超时（>{timeout}s）"
        except BudgetExceededError:
            raise
        except Exception as e:
            logger.error("Reviewer: 评审失败", error=str(e), task_id=state["task_id"])
            # 评审失败不丢弃产出：直接汇总各 Agent 结果作为最终交付内容
            parts = [
                f"[{r.get('agent_name') or r.get('role', '?')}] "
                f"{r.get('result') or r.get('error') or '（无产出）'}"
                for r in agent_results
                if r.get("result") or r.get("error")
            ]
            final_result = (
                "多 Agent 执行完成，但最终评审失败，以下为各 Agent 产出汇总（参考）：\n"
                + ("\n".join(parts) if parts else "（各 Agent 均无产出）")
                + f"\n评审错误: {e}"
            )
            error = str(e)

        logger.info(
            "Reviewer: 评审完成",
            task_id=state["task_id"],
            length=len(final_result),
        )
        get_trace_recorder().record_agent_step(
            state["task_id"],
            AgentStepEvent(
                agent_name="Reviewer",
                parent_agent="Supervisor",
                input=ctx.original_user_query,
                context_snapshot=ctx.to_state_dict(),
                extracted_requirements=dict(ctx.extracted_requirements),
                missing_requirements=list(ctx.missing_requirements),
                output=final_result,
                error=error,
                latency_ms=(time.perf_counter() - t0) * 1000,
            ),
        )
        return {
            "final_result": final_result,
            "messages": [AIMessage(content="Review completed")],
            "errors": [],
        }
