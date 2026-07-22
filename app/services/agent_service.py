"""
Agent 执行服务。
封装 LangGraph Workflow，提供任务执行入口。
"""

from __future__ import annotations

from typing import Any

from app.agent.state import AgentState
from app.agent.workflow import AgentWorkflow
from app.config.logging import get_logger
from app.config.settings import Settings, get_settings
from app.llm.factory import LLMProviderFactory
from app.memory.base import BaseMemory
from app.models.task import TaskStatus
from app.prompts.manager import PromptManager
from app.services.task_service import TaskService

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

    async def _recall_memory(self, goal: str) -> str | None:
        """从长期记忆检索与当前目标相关的历史，拼接为额外上下文。"""
        memory = self._get_long_term_memory()
        if memory is None:
            return None
        try:
            results = await memory.search(goal, top_k=3)
        except Exception as e:  # pragma: no cover - 依赖向量库/嵌入
            logger.warning("长期记忆检索失败", error=str(e))
            return None
        if not results:
            return None
        lines = [str(item.get("value", "")) for item in results if item.get("value")]
        if not lines:
            return None
        return "\n".join(f"- {line}" for line in lines)

    async def _remember_result(self, task_id: str, goal: str, result: str) -> None:
        """将任务结果摘要写入长期记忆。"""
        memory = self._get_long_term_memory()
        if memory is None:
            return
        try:
            summary = f"目标: {goal}\n结果: {result}"
            await memory.save(f"task:{task_id}", summary)
        except Exception as e:  # pragma: no cover - 依赖向量库/嵌入
            logger.warning("写入长期记忆失败", error=str(e))

    def _get_workflow(self) -> Any:
        """懒加载编译 Workflow。"""
        if self._workflow is None:
            llm_provider = LLMProviderFactory.create()
            prompt_manager = PromptManager
            workflow_builder = AgentWorkflow(llm_provider, prompt_manager)
            self._workflow = workflow_builder.build()
        return self._workflow

    async def run_task(
        self,
        task_id: str,
        goal: str,
        context: str | None = None,
    ) -> str | None:
        """
        启动 Agent Workflow 执行任务。

        使用 LangGraph 的 astream 实现流式状态更新，
        每步执行后持久化状态到 TaskService。

        Args:
            task_id: 任务 ID。
            goal: 用户目标。
            context: 可选上下文信息。

        Returns:
            最终结果文本，失败返回 None。
        """
        logger.info("AgentService: 开始执行任务", task_id=task_id, goal=goal)

        # 更新任务状态为规划中
        await self.task_service.update_task_status(task_id, TaskStatus.PLANNING)

        # 从长期记忆检索相关历史，注入 context
        recalled = await self._recall_memory(goal)
        effective_context = context
        if recalled:
            prefix = "[相关历史记忆]\n" + recalled
            effective_context = (
                f"{prefix}\n\n{context}" if context else prefix
            )
            logger.info("AgentService: 注入长期记忆上下文", task_id=task_id)

        # 构造初始状态
        initial_state: AgentState = {
            "goal": goal,
            "context": effective_context,
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

        workflow = self._get_workflow()
        final_result = None

        try:
            async for event in workflow.astream(initial_state):
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
                    elif node_name == "executor":
                        await self.task_service.update_task_status(
                            task_id, TaskStatus.EXECUTING
                        )
                    elif node_name == "reflection":
                        await self.task_service.update_task_status(
                            task_id, TaskStatus.REFLECTING
                        )
                        if node_output.get("should_replan"):
                            await self.task_service.update_task_status(
                                task_id, TaskStatus.REPLANNING
                            )
                    elif node_name == "replanner":
                        await self.task_service.update_task_status(
                            task_id, TaskStatus.EXECUTING
                        )

                    # 检查是否有最终结果
                    if node_output.get("final_result"):
                        final_result = node_output["final_result"]

            # 执行完成
            status = TaskStatus.COMPLETED if final_result else TaskStatus.FAILED
            await self.task_service.update_task_status(
                task_id, status, final_result=final_result
            )

            # 任务成功时写入长期记忆
            if final_result:
                await self._remember_result(task_id, goal, final_result)

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
