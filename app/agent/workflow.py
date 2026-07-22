"""
LangGraph Agent Workflow 状态机。
构建 Planner -> Executor -> Reflection 的完整工作流。
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from app.agent.executor_node import ExecutorNode
from app.agent.planner_node import PlannerNode
from app.agent.reflection_node import ReflectionNode
from app.agent.state import AgentState
from app.config.logging import get_logger
from app.config.settings import get_settings
from app.llm.base import BaseLLMProvider
from app.prompts.manager import PromptManager

logger = get_logger(__name__)


class AgentWorkflow:
    """
    Agent Workflow 构建器。

    构建 Planner -> Executor -> Reflection 的 LangGraph 状态机，
    支持条件路由实现反思驱动的重新规划循环。

    流程:
        START -> [Planner] -> [Executor] -> [Reflection]
                                ^                |
                                |   (还有任务)    |
                                +----------------+
                                |                |
                                | (不满意+未超限)  |
                                +--[Replanner]---+
                                                 |
                                             (完成/超限) -> END
    """

    def __init__(
        self,
        llm_provider: BaseLLMProvider,
        prompt_manager: PromptManager,
    ):
        self.planner = PlannerNode(llm_provider, prompt_manager)
        self.executor = ExecutorNode(llm_provider, prompt_manager)
        self.reflection = ReflectionNode(llm_provider, prompt_manager)
        self.settings = get_settings()

    def build(self) -> StateGraph:
        """
        构建并编译 LangGraph 状态机。

        Returns:
            编译后的 StateGraph 实例。
        """
        graph = StateGraph(AgentState)

        # 注册节点
        graph.add_node("planner", self.planner.run)
        graph.add_node("executor", self.executor.run)
        graph.add_node("reflection", self.reflection.run)
        graph.add_node("replanner", self.planner.replan)

        # 定义边
        graph.add_edge(START, "planner")  # 入口 -> Planner
        graph.add_edge("planner", "executor")  # Planner -> Executor
        graph.add_edge("executor", "reflection")  # Executor -> Reflection

        # 条件边：Reflection 决定下一步
        graph.add_conditional_edges(
            "reflection",
            self._route_after_reflection,
            {
                "replan": "replanner",  # 不满意 -> 重新规划
                "continue": "executor",  # 还有未完成任务 -> 继续执行
                "complete": END,  # 全部完成 -> 结束
            },
        )

        # 重新规划后回到 Executor
        graph.add_edge("replanner", "executor")

        logger.info("AgentWorkflow: 状态机编译完成")
        return graph.compile()

    def _route_after_reflection(self, state: AgentState) -> str:
        """
        Reflection 后的路由决策。

        逻辑：
        1. 达到最大迭代次数 -> 强制完成
        2. Reflection 发现问题且未超限 -> 重新规划
        3. 还有未完成的子任务 -> 继续执行
        4. 所有任务完成且质量达标 -> 完成

        Args:
            state: 当前 Agent 状态。

        Returns:
            路由目标名称："replan" / "continue" / "complete"
        """
        # 达到最大迭代次数，强制结束
        if state["iteration_count"] >= self.settings.MAX_REPLAN_ITERATIONS:
            logger.warning(
                "Workflow: 达到最大迭代次数，强制结束",
                iterations=state["iteration_count"],
            )
            return "complete"

        # Reflection 发现问题，触发重新规划
        if state.get("should_replan", False):
            logger.info("Workflow: 触发重新规划")
            return "replan"

        # 检查是否还有未完成的子任务
        plan = state.get("plan")
        if plan and "subtasks" in plan:
            remaining = len(plan["subtasks"]) - state["current_task_index"]
            if remaining > 0:
                logger.info("Workflow: 继续执行剩余子任务", remaining=remaining)
                return "continue"

        # 所有任务完成
        logger.info("Workflow: 所有任务完成")
        return "complete"
