"""
Agent 执行服务。
封装 LangGraph Workflow，提供任务执行入口。
"""

from __future__ import annotations

from typing import Any

import structlog

from app.agent.state import AgentState
from app.agent.workflow import AgentWorkflow
from app.config.logging import get_logger
from app.config.settings import Settings, get_settings
from app.llm.factory import LLMProviderFactory
from app.memory.base import BaseMemory
from app.models.task import TaskStatus
from app.prompts.manager import PromptManager
from app.services.task_service import TaskService
from app.tools.security import ROLE_ADMIN, ToolContext

logger = get_logger(__name__)


class AgentService:
    """
    Agent Workflow 执行服务。

    封装 LangGraph 状态机，提供：
    - run_task(): 启动 Workflow 执行任务
    - 流式状态更新和持久化
    """

    def __init__(
        self,
        task_service: TaskService,
        settings: Settings | None = None,
        long_term_memory: BaseMemory | None = None,
    ):
        self.task_service = task_service
        self._settings = settings or get_settings()
        self._long_term_memory = long_term_memory
        self._workflow = None

    def _get_long_term_memory(self) -> BaseMemory | None:
        """惰性初始化长期记忆（仅在开关开启时）。"""
        if not self._settings.ENABLE_LONG_TERM_MEMORY:
            return None
        if self._long_term_memory is None:
            from app.memory.factory import MemoryFactory

            self._long_term_memory = MemoryFactory.create_long_term(self._settings)
        return self._long_term_memory

    async def _recall_memory(
        self, goal: str, tool_context: ToolContext
    ) -> str | None:
        """从长期记忆检索与当前目标相关的历史（限定调用者作用域），拼接为额外上下文。"""
        memory = self._get_long_term_memory()
        if memory is None:
            return None
        try:
            results = await memory.search(
                goal,
                top_k=3,
                user_id=tool_context.user_id,
                tenant_id=tool_context.tenant_id,
            )
        except Exception as e:  # pragma: no cover - 依赖向量库/嵌入
            logger.warning("长期记忆检索失败", error=str(e))
            return None
        if not results:
            return None
        lines = [str(item.get("value", "")) for item in results if item.get("value")]
        if not lines:
            return None
        return "\n".join(f"- {line}" for line in lines)

    async def _remember_result(
        self,
        task_id: str,
        goal: str,
        result: str,
        tool_context: ToolContext,
    ) -> None:
        """将任务结果摘要写入长期记忆（归属调用者作用域）。"""
        memory = self._get_long_term_memory()
        if memory is None:
            return
        try:
            summary = f"目标: {goal}\n结果: {result}"
            await memory.save(
                f"task:{task_id}",
                summary,
                user_id=tool_context.user_id,
                tenant_id=tool_context.tenant_id,
            )
        except Exception as e:  # pragma: no cover - 依赖向量库/嵌入
            logger.warning("写入长期记忆失败", error=str(e))

    async def _get_workflow(self) -> Any:
        """懒加载编译 Workflow（启用 LangGraph Checkpoint，thread_id=task_id）。"""
        if self._workflow is None:
            from app.agent.checkpoint import create_checkpointer

            llm_provider = LLMProviderFactory.create()
            prompt_manager = PromptManager
            workflow_builder = AgentWorkflow(llm_provider, prompt_manager)
            checkpointer = await create_checkpointer(self._settings)
            self._workflow = workflow_builder.build(checkpointer=checkpointer)
        return self._workflow

    async def run_task(
        self,
        task_id: str,
        goal: str,
        context: str | None = None,
        tool_context: ToolContext | None = None,
    ) -> str | None:
        """
        启动 Agent Workflow 执行任务。

        使用 LangGraph 的 astream 实现流式状态更新，
        每步执行后持久化状态到 TaskService。

        Args:
            task_id: 任务 ID。
            goal: 用户目标。
            context: 可选上下文信息。
            tool_context: 调用者身份上下文；缺省视为内部调用（admin 角色）。

        Returns:
            最终结果文本，失败返回 None。
        """
        # 绑定 task_id 到日志上下文，下游节点日志经 merge_contextvars 自动携带，
        # with 退出（含异常路径）时自动清理，避免跨任务残留。
        with structlog.contextvars.bound_contextvars(task_id=task_id):
            return await self._run_task_inner(task_id, goal, context, tool_context)

    async def _run_task_inner(
        self,
        task_id: str,
        goal: str,
        context: str | None = None,
        tool_context: ToolContext | None = None,
    ) -> str | None:
        """run_task 的实际执行体，在已绑定 task_id 的日志上下文内运行。"""
        logger.info("AgentService: 开始执行任务", task_id=task_id, goal=goal)

        # 调用者身份：缺省视为内部调用（admin 角色）
        caller = tool_context or ToolContext(role=ROLE_ADMIN)
        # LangGraph Checkpoint 线程键：thread_id=task_id，崩溃后可断点续跑
        run_config = {"configurable": {"thread_id": task_id}}

        # 更新任务状态为规划中
        await self.task_service.update_task_status(task_id, TaskStatus.PLANNING)

        # 从长期记忆检索相关历史（限定调用者作用域），注入 context（以外部数据标记包裹，防注入）
        recalled = await self._recall_memory(goal, caller)
        effective_context = context
        if recalled:
            prefix = (
                "<external_knowledge>\n[相关历史记忆]\n"
                f"{recalled}\n</external_knowledge>"
            )
            effective_context = (
                f"{prefix}\n\n{context}" if context else prefix
            )
            logger.info("AgentService: 注入长期记忆上下文", task_id=task_id)

        # 构造初始状态
        initial_state: AgentState = {
            "goal": goal,
            "context": effective_context,
            "tool_context": caller.to_dict(),
            "plan": None,
            "plan_version": 0,
            "current_task_index": 0,
            "task_results": [],
            "reflection_result": None,
            "should_replan": False,
            "iteration_count": 0,
            "final_result": None,
            "task_id": task_id,
            "messages": [],
            "errors": [],
        }

        workflow = await self._get_workflow()
        final_result = None

        try:
            # 断点续跑：若该 thread 存在未完成的检查点（服务崩溃/中断后重提），
            # 以 None 作为输入从检查点继续执行，已完成节点（如 Planner）不会重跑；
            # 全新任务则携带完整初始状态启动。
            snapshot = await workflow.aget_state(run_config)
            resume = snapshot is not None and bool(snapshot.next)
            stream_input = None if resume else initial_state
            if resume:
                logger.info(
                    "AgentService: 检测到未完成检查点，断点续跑",
                    task_id=task_id,
                    pending=snapshot.next,
                )

            async for event in workflow.astream(stream_input, config=run_config):
                # 每个节点执行后更新任务状态
                for node_name, node_output in event.items():
                    logger.info(
                        "AgentService: 节点执行完成",
                        task_id=task_id,
                        node=node_name,
                    )

                    # 根据节点更新任务状态
                    if node_name == "planner":
                        await self.task_service.update_task_status(
                            task_id, TaskStatus.EXECUTING
                        )
                        if node_output.get("plan"):
                            await self.task_service.sync_plan(
                                task_id, node_output["plan"]
                            )
                    elif node_name == "executor":
                        await self.task_service.update_task_status(
                            task_id, TaskStatus.EXECUTING
                        )
                        if node_output.get("task_results"):
                            await self.task_service.sync_task_results(
                                task_id, node_output["task_results"]
                            )
                    elif node_name == "reflection":
                        await self.task_service.update_task_status(
                            task_id, TaskStatus.REFLECTING
                        )
                        await self.task_service.sync_reflection(
                            task_id,
                            node_output.get("reflection_result"),
                            node_output.get("iteration_count"),
                        )
                        if node_output.get("should_replan"):
                            await self.task_service.update_task_status(
                                task_id, TaskStatus.REPLANNING
                            )
                    elif node_name == "replanner":
                        await self.task_service.update_task_status(
                            task_id, TaskStatus.EXECUTING
                        )
                        if node_output.get("plan"):
                            await self.task_service.sync_plan(
                                task_id, node_output["plan"]
                            )
                        if node_output.get("iteration_count") is not None:
                            await self.task_service.sync_reflection(
                                task_id, None, node_output["iteration_count"]
                            )

                    # 检查是否有最终结果
                    if node_output.get("final_result"):
                        final_result = node_output["final_result"]

            # 从检查点读取最终状态：断点续跑/已完结线程可能不产出节点事件
            if final_result is None:
                state = await workflow.aget_state(run_config)
                if state is not None and state.values:
                    final_result = state.values.get("final_result")

            # 执行完成
            status = TaskStatus.COMPLETED if final_result else TaskStatus.FAILED
            await self.task_service.update_task_status(
                task_id, status, final_result=final_result
            )

            # 任务成功时写入长期记忆（归属调用者作用域）
            if final_result:
                await self._remember_result(task_id, goal, final_result, caller)

            logger.info(
                "AgentService: 任务执行完成",
                task_id=task_id,
                status=status.value,
            )

            return final_result

        except Exception as e:
            logger.error("AgentService: 任务执行异常", task_id=task_id, error=str(e))
            await self.task_service.update_task_status(
                task_id,
                TaskStatus.FAILED,
                error=str(e),
            )
            return None
