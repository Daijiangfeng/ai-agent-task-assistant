"""
Reflection Agent 节点。
负责评估执行结果的质量，决定是否触发重新规划。
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import AIMessage
from langchain_core.output_parsers import JsonOutputParser

from app.agent.state import AgentState
from app.config.logging import get_logger
from app.config.settings import get_settings
from app.llm.base import BaseLLMProvider
from app.prompts.manager import PromptManager

logger = get_logger(__name__)


class ReflectionNode:
    """
    Reflection Agent 节点。

    职责：
    - 评估执行结果的准确性、完整性、相关性
    - 检测是否存在幻觉
    - 决定是否需要重新规划
    - 如果全部完成，合成最终结果
    """

    def __init__(self, llm_provider: BaseLLMProvider, prompt_manager: PromptManager):
        self.llm = llm_provider.get_chat_model()
        self.prompt_manager = prompt_manager
        self.settings = get_settings()

    async def run(self, state: AgentState) -> dict[str, Any]:
        """
        评估当前执行结果。

        Args:
            state: 当前 Agent 状态。

        Returns:
            状态更新字典。
        """
        logger.info("Reflection: 开始评估执行结果")

        plan = state["plan"]
        task_results = state["task_results"]

        # 检查是否还有未完成的子任务
        subtasks = plan.get("subtasks", []) if plan else []
        all_completed = state["current_task_index"] >= len(subtasks)

        if not all_completed:
            # 还有子任务未执行，不需要反思，直接继续
            logger.info(
                "Reflection: 还有未完成任务，跳过评估",
                remaining=len(subtasks) - state["current_task_index"],
            )
            return {
                "reflection_result": None,
                "should_replan": False,
                "final_result": None,
                "messages": [AIMessage(content="More subtasks to execute, skip reflection")],
                "errors": [],
            }

        # 所有子任务已完成，进行反思评估
        prompt = self.prompt_manager.get_reflection_prompt()
        parser = JsonOutputParser()
        chain = prompt | self.llm | parser

        try:
            reflection = await chain.ainvoke({
                "goal": state["goal"],
                "plan": json.dumps(plan, ensure_ascii=False),
                "task_results": json.dumps(task_results, ensure_ascii=False),
            })

            logger.info(
                "Reflection: 评估完成",
                is_satisfactory=reflection.get("is_satisfactory"),
                accuracy=reflection.get("accuracy_score"),
                completeness=reflection.get("completeness_score"),
                relevance=reflection.get("relevance_score"),
            )

            # 判断是否需要重新规划
            is_satisfactory = reflection.get("is_satisfactory", False)
            max_iterations = self.settings.MAX_REPLAN_ITERATIONS
            should_replan = (
                not is_satisfactory
                and state["iteration_count"] < max_iterations
            )

            # 如果满意或达到最大迭代次数，合成最终结果
            final_result = None
            if not should_replan:
                final_result = self._synthesize_results(task_results, state["goal"])

            return {
                "reflection_result": reflection,
                "should_replan": should_replan,
                "final_result": final_result,
                "messages": [
                    AIMessage(
                        content=f"Reflection: satisfactory={is_satisfactory}, "
                        f"replan={should_replan}"
                    )
                ],
                "errors": [],
            }

        except Exception as e:
            logger.error("Reflection: 评估失败", error=str(e))

            # 评估失败时，默认通过并合成结果
            final_result = self._synthesize_results(task_results, state["goal"])

            return {
                "reflection_result": {
                    "is_satisfactory": True,
                    "accuracy_score": 0.5,
                    "completeness_score": 0.5,
                    "relevance_score": 0.5,
                    "issues": [f"Reflection evaluation failed: {str(e)}"],
                    "suggestion": None,
                },
                "should_replan": False,
                "final_result": final_result,
                "messages": [],
                "errors": [f"Reflection failed: {str(e)}"],
            }

    def _synthesize_results(self, task_results: list[dict], goal: str) -> str:
        """
        合成所有子任务结果为最终报告。

        Args:
            task_results: 所有子任务结果列表。
            goal: 用户原始目标。

        Returns:
            最终结果文本。
        """
        lines = [f"# 任务执行结果\n\n**目标**: {goal}\n"]

        for i, r in enumerate(task_results, 1):
            status = r.get("status", "unknown")
            result = r.get("result", "无结果")
            desc = r.get("description", "")

            lines.append(f"## 步骤 {i}: {desc}")
            lines.append(f"**状态**: {status}\n")
            if result:
                lines.append(f"**结果**:\n{result}\n")
            if r.get("error"):
                lines.append(f"**错误**: {r['error']}\n")

        return "\n".join(lines)
