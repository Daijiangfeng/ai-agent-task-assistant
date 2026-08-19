"""
Planner Agent 节点。
负责根据用户目标生成结构化执行计划，以及基于反思结果重新规划。
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import AIMessage
from langchain_core.output_parsers import JsonOutputParser

from app.agent.state import AgentState
from app.config.logging import get_logger
from app.llm.base import BaseLLMProvider
from app.llm.budget import BudgetExceededError, budgeted_ainvoke
from app.prompts.manager import PromptManager
from app.tools.registry import ToolRegistry

logger = get_logger(__name__)


class PlannerNode:
    """
    Planner Agent 节点。

    职责：
    - run(): 首次规划，将用户目标拆解为子任务序列
    - replan(): 重新规划，基于反思结果调整计划
    """

    def __init__(self, llm_provider: BaseLLMProvider, prompt_manager: PromptManager):
        self.llm = llm_provider.get_chat_model()
        self.prompt_manager = prompt_manager

    async def run(self, state: AgentState) -> dict[str, Any]:
        """
        首次规划：将用户目标拆解为子任务序列。

        Args:
            state: 当前 Agent 状态。

        Returns:
            状态更新字典。
        """
        # 重试路径：携带 retry_from_index 时复用已有计划，跳过 LLM 规划，
        # 从指定索引继续执行（截断该索引之后的任务结果）。
        retry_from_index = state.get("retry_from_index")
        if retry_from_index is not None and state.get("plan"):
            logger.info(
                "Planner: 重试模式，复用现有计划",
                from_index=retry_from_index,
                version=state["plan_version"],
            )
            return {
                "current_task_index": retry_from_index,
                "task_results": list(state.get("task_results", []))[
                    :retry_from_index
                ],
                "retry_from_index": None,
                "should_replan": False,
                "errors": [],
            }

        logger.info("Planner: 开始规划", goal=state["goal"])

        # 获取可用工具描述
        available_tools = ToolRegistry.get_tool_descriptions()

        # 获取 Prompt 模板并构造链
        prompt = self.prompt_manager.get_planner_prompt()
        parser = JsonOutputParser()
        chain = prompt | self.llm | parser

        try:
            plan = await budgeted_ainvoke(chain, {
                "goal": state["goal"],
                "context": state.get("context") or "无",
                "available_tools": available_tools,
            })

            logger.info(
                "Planner: 规划完成",
                subtask_count=len(plan.get("subtasks", [])),
            )

            return {
                "plan": plan,
                "plan_version": 1,
                "current_task_index": 0,
                "task_results": [],
                "iteration_count": 0,
                "should_replan": False,
                "reflection_result": None,
                "final_result": None,
                "messages": [
                    AIMessage(
                        content=f"Plan created with {len(plan.get('subtasks', []))} subtasks"
                    )
                ],
                "errors": [],
            }

        except BudgetExceededError:
            raise
        except Exception as e:
            logger.error("Planner: 规划失败", error=str(e))
            return {
                "plan": None,
                "plan_version": 0,
                "current_task_index": 0,
                "task_results": [],
                "iteration_count": 0,
                "should_replan": False,
                "reflection_result": None,
                "final_result": None,
                "messages": [],
                "errors": [f"Planner failed: {str(e)}"],
            }

    async def replan(self, state: AgentState) -> dict[str, Any]:
        """
        重新规划：基于反思结果调整计划。

        Args:
            state: 当前 Agent 状态。

        Returns:
            状态更新字典。
        """
        logger.info("Replanner: 开始重新规划", version=state["plan_version"])

        available_tools = ToolRegistry.get_tool_descriptions()

        prompt = self.prompt_manager.get_replanner_prompt()
        parser = JsonOutputParser()
        chain = prompt | self.llm | parser

        try:
            new_plan = await budgeted_ainvoke(chain, {
                "goal": state["goal"],
                "original_plan": json.dumps(state["plan"], ensure_ascii=False),
                "task_results": json.dumps(state["task_results"], ensure_ascii=False),
                "reflection": json.dumps(state["reflection_result"], ensure_ascii=False),
                "available_tools": available_tools,
            })

            new_version = state["plan_version"] + 1
            logger.info(
                "Replanner: 重新规划完成",
                version=new_version,
                subtask_count=len(new_plan.get("subtasks", [])),
            )

            return {
                "plan": new_plan,
                "plan_version": new_version,
                "current_task_index": 0,
                "should_replan": False,
                "iteration_count": state["iteration_count"] + 1,
                "messages": [
                    AIMessage(content=f"Replan completed (version {new_version})")
                ],
                "errors": [],
            }

        except BudgetExceededError:
            raise
        except Exception as e:
            logger.error("Replanner: 重新规划失败", error=str(e))
            return {
                "should_replan": False,
                "iteration_count": state["iteration_count"] + 1,
                "final_result": f"重新规划失败，终止执行。错误: {str(e)}",
                "messages": [],
                "errors": [f"Replanner failed: {str(e)}"],
            }
