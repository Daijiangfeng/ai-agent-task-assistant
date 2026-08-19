"""
LangGraph Agent Workflow 状态机。
构建 Supervisor(多 Agent 协作) + Planner -> Executor -> Reflection 的完整工作流。
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from app.agent.executor_node import ExecutorNode
from app.agent.multi_agent import MODE_MULTI_AGENT, ReviewerNode, SubAgentsNode, SupervisorNode
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

    支持两种执行模式：
    1. 多 Agent 协作（Supervisor 模式）：用户目标经 Supervisor 编排，
       分配给 Research/Data/Coding/Writing/Review 子 Agent 协作执行，
       由 Reviewer 合成最终结果；
    2. 单 Agent 流程（默认）：Planner -> Executor -> Reflection 状态机，
       支持反思驱动的重新规划循环。

    流程:
        START -> [Supervisor] --multi_agent--> [SubAgents] -> [Reviewer] -> END
                                  | single
                                  v
                              [Planner] -> [Executor] -> [Reflection]
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
        approval_gate=None,
    ):
        self.supervisor = SupervisorNode(llm_provider, prompt_manager)
        self.sub_agents = SubAgentsNode(llm_provider, prompt_manager, approval_gate)
        self.reviewer = ReviewerNode(llm_provider, prompt_manager)
        self.planner = PlannerNode(llm_provider, prompt_manager)
        self.executor = ExecutorNode(llm_provider, prompt_manager, approval_gate=approval_gate)
        self.reflection = ReflectionNode(llm_provider, prompt_manager)
        self.settings = get_settings()

    def build(self, checkpointer=None) -> StateGraph:
        """
        构建并编译 LangGraph 状态机。

        Args:
            checkpointer: 可选的 LangGraph checkpointer 实例，
                          启用后支持长时间任务的断点恢复（如 MemorySaver、
                          SqliteSaver 等）。未提供时不启用检查点。

        Returns:
            编译后的 StateGraph 实例。
        """
        graph = StateGraph(AgentState)

        # 注册节点
        graph.add_node("supervisor", self.supervisor.run)
        graph.add_node("sub_agents", self.sub_agents.run)
        graph.add_node("reviewer", self.reviewer.run)
        graph.add_node("planner", self.planner.run)
        graph.add_node("executor", self.executor.run)
        graph.add_node("reflection", self.reflection.run)
        graph.add_node("replanner", self.planner.replan)

        # 定义边
        graph.add_edge(START, "supervisor")  # 入口 -> Supervisor

        # 条件边：Supervisor 决定执行模式
        graph.add_conditional_edges(
            "supervisor",
            self._route_after_supervisor,
            {
                "multi_agent": "sub_agents",  # 多 Agent 协作
                "single": "planner",  # 单 Agent 流程
            },
        )

        # 多 Agent 协作：子 Agent -> Reviewer -> 结束
        graph.add_edge("sub_agents", "reviewer")
        graph.add_edge("reviewer", END)

        # 单 Agent 流程：Planner -> Executor -> Reflection
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

        compile_kwargs = {}
        if checkpointer is not None:
            compile_kwargs["checkpointer"] = checkpointer
            logger.info("AgentWorkflow: 启用检查点持久化 (%s)", type(checkpointer).__name__)

        logger.info("AgentWorkflow: 状态机编译完成")
        return graph.compile(**compile_kwargs)

    def _route_after_supervisor(self, state: AgentState) -> str:
        """
        Supervisor 后的路由决策。

        Returns:
            "multi_agent"（多 Agent 协作）或 "single"（单 Agent 流程）。
        """
        mode = state.get("execution_mode") or "single"
        mode = str(mode).strip().lower()
        if mode == MODE_MULTI_AGENT and state.get("agent_assignments"):
            logger.info("Workflow: 进入多 Agent 协作")
            return "multi_agent"
        logger.info("Workflow: 进入单 Agent 流程")
        return "single"

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
