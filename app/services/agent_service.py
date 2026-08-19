"""
Agent 执行服务。
封装 LangGraph Workflow，提供任务执行入口。

能力：
- run_task(): 启动 Workflow 执行任务（支持断点续跑 / 从某一步重试）；
- resume_approval(): Human-in-the-loop 审批决策后恢复执行；
- 流式状态更新和持久化；
- 节点边界响应暂停 / 取消控制请求；
- 处理 LangGraph interrupt（工具审批）事件，持久化审批请求并置任务为
  awaiting_approval。
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any

import structlog

from app.agent.state import AgentState
from app.agent.workflow import AgentWorkflow
from app.config.logging import get_logger
from app.config.settings import Settings, get_settings
from app.llm.budget import BudgetExceededError, UsageSnapshot, budget_scope
from app.llm.factory import LLMProviderFactory
from app.memory.base import BaseMemory
from app.models.task import ApprovalRequest, TaskStatus
from app.prompts.manager import PromptManager
from app.services.task_control import TaskControlService
from app.services.task_service import TaskService
from app.tools.security import ROLE_ADMIN, ToolContext
from app.tracing.recorder import get_trace_recorder

logger = get_logger(__name__)

APPROVAL_REQUEST_KIND = "tool_approval"


def _utcnow_iso() -> str:
    """返回当前 UTC 时间的 ISO 8601 字符串。"""
    return datetime.now(timezone.utc).isoformat()


class AgentService:
    """
    Agent Workflow 执行服务。

    封装 LangGraph 状态机，提供：
    - run_task(): 启动 Workflow 执行任务（含断点续跑与重试）
    - resume_approval(): 审批决策后恢复执行
    - 流式状态更新和持久化
    - 暂停 / 取消控制响应
    """

    def __init__(
        self,
        task_service: TaskService,
        settings: Settings | None = None,
        long_term_memory: BaseMemory | None = None,
        approval_gate=None,
    ):
        self.task_service = task_service
        self._settings = settings or get_settings()
        self._long_term_memory = long_term_memory
        # Human-in-the-loop 审批闸门；注入后副作用工具调用会暂停等待用户决策
        self._approval_gate = approval_gate
        self._workflow = None
        self._control: TaskControlService | None = None

    def _get_control(self) -> TaskControlService:
        """懒加载任务控制服务单例。"""
        if self._control is None:
            from app.services.task_control import get_task_control

            self._control = get_task_control()
        return self._control

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
            workflow_builder = AgentWorkflow(
                llm_provider, prompt_manager, approval_gate=self._approval_gate
            )
            checkpointer = await create_checkpointer(self._settings)
            self._workflow = workflow_builder.build(checkpointer=checkpointer)
        return self._workflow

    async def _delete_thread(self, workflow: Any, thread_id: str) -> None:
        """删除检查点线程（重试时保证全新执行；不支持时忽略）。"""
        checkpointer = getattr(workflow, "checkpointer", None)
        if checkpointer is None:
            return
        for name in ("adelete_thread", "delete_thread"):
            fn = getattr(checkpointer, name, None)
            if fn is None:
                continue
            try:
                if asyncio.iscoroutinefunction(fn):
                    await fn(thread_id)
                else:
                    fn(thread_id)
                logger.info("AgentService: 已删除检查点线程", thread_id=thread_id)
                return
            except Exception as e:  # pragma: no cover - 防御性
                logger.warning("AgentService: 删除检查点线程失败", error=str(e))
                return
        logger.warning("AgentService: 检查点不支持删除线程，重试可能延续旧进度")

    # ------------------------------------------------------------------
    # 任务执行入口
    # ------------------------------------------------------------------

    async def run_task(
        self,
        task_id: str,
        goal: str,
        context: str | None = None,
        tool_context: ToolContext | None = None,
        retry_from_index: int | None = None,
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
            retry_from_index: 从指定子任务索引重新执行（基于已有计划）；
                None 表示从头（含规划）执行或断点续跑。

        Returns:
            最终结果文本，失败返回 None。
        """
        # 绑定 task_id 到日志上下文，下游节点日志经 merge_contextvars 自动携带，
        # with 退出（含异常路径）时自动清理，避免跨任务残留。
        with structlog.contextvars.bound_contextvars(task_id=task_id):
            return await self._run_task_inner(
                task_id,
                goal,
                context,
                tool_context,
                retry_from_index=retry_from_index,
            )

    async def _run_task_inner(
        self,
        task_id: str,
        goal: str,
        context: str | None = None,
        tool_context: ToolContext | None = None,
        retry_from_index: int | None = None,
    ) -> str | None:
        """run_task 的实际执行体，在已绑定 task_id 的日志上下文内运行。"""
        logger.info("AgentService: 开始执行任务", task_id=task_id, goal=goal)
        recorder = get_trace_recorder()

        # 调用者身份：缺省视为内部调用（admin 角色）
        caller = tool_context or ToolContext(role=ROLE_ADMIN)
        # LangGraph Checkpoint 线程键：thread_id=task_id，崩溃后可断点续跑
        run_config = {"configurable": {"thread_id": task_id}}
        control = self._get_control()

        # 启动 Trace（OpenTelemetry 风格记录，供执行监控/成本核算/审计）
        if recorder.get_trace(task_id) is None:
            recorder.start_run(
                task_id=task_id,
                goal=goal,
                user_id=caller.user_id,
                tenant_id=caller.tenant_id,
            )

        # 重试：删除该线程检查点，保证从指定索引全新执行
        workflow = await self._get_workflow()
        if retry_from_index is not None:
            await self._delete_thread(workflow, task_id)
            control.clear(task_id)

        # 更新任务状态为规划中（续跑/重试时节点执行会继续更新状态）
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

        # 重试时从任务记录还原已完成子任务结果（供后续子任务引用）
        previous_results: list[dict[str, Any]] = []
        if retry_from_index is not None:
            task = await self.task_service.get_task(task_id)
            if task is not None:
                previous_results = [
                    {
                        "subtask_id": s.id,
                        "description": s.description,
                        "result": s.result,
                        "status": "completed",
                    }
                    for s in task.subtasks
                    if s.status == TaskStatus.COMPLETED and s.result
                ]

        # 构造初始状态
        # 统一上下文：original_user_query 完整保留用户输入，extracted_requirements
        # 由确定性规则提取（非 LLM 猜测），供 Supervisor/SubAgent/Reviewer 全链路透传。
        from app.agent.requirements import extract_requirements

        extracted = extract_requirements(f"{goal}\n{effective_context or ''}")
        initial_state: AgentState = {
            "goal": goal,
            "context": effective_context,
            "original_user_query": goal,
            "conversation_history": [{"role": "user", "content": goal}],
            "extracted_requirements": extracted,
            "missing_requirements": [],
            "intermediate_results": [],
            "tool_results": [],
            "subagent_results": [],
            "tool_context": caller.to_dict(),
            "plan": None,
            "plan_version": 0,
            "current_task_index": 0,
            "task_results": previous_results,
            "reflection_result": None,
            "should_replan": False,
            "iteration_count": 0,
            "execution_mode": None,
            "agent_assignments": [],
            "agent_results": [],
            "retry_from_index": retry_from_index,
            "final_result": None,
            "task_id": task_id,
            "messages": [],
            "errors": [],
        }

        final_result = None
        stop_reason: str | None = None
        interrupt_payload: dict[str, Any] | None = None

        try:
            # 进入单任务 LLM 预算上下文：节点内所有 LLM 调用计入预算，
            # 超限（次数/token/金额）抛 BudgetExceededError 终止任务。
            async with budget_scope(
                max_llm_calls=self._settings.MAX_LLM_CALLS_PER_TASK,
                max_total_tokens=self._settings.MAX_TOTAL_TOKENS_PER_TASK,
                budget_limit_usd=self._settings.BUDGET_LIMIT_USD,
                input_cost_per_1m=self._settings.LLM_INPUT_COST_PER_1M,
                output_cost_per_1m=self._settings.LLM_OUTPUT_COST_PER_1M,
            ) as budget:
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

                prev_usage = UsageSnapshot()
                prev_time = time.perf_counter()
                async for event in workflow.astream(stream_input, config=run_config):
                    # LangGraph interrupt：工具审批等暂停点，等待用户决策后恢复
                    if "__interrupt__" in event:
                        for item in event["__interrupt__"]:
                            payload = getattr(item, "value", None) or {}
                            is_approval = (
                                isinstance(payload, dict)
                                and payload.get("kind") == APPROVAL_REQUEST_KIND
                            )
                            if is_approval:
                                interrupt_payload = payload
                                stop_reason = "interrupt"
                                break
                        if stop_reason == "interrupt":
                            break

                    # 每个节点执行后：更新任务状态 + 记录节点级 Trace span
                    for node_name, node_output in event.items():
                        now = time.perf_counter()
                        usage = budget.snapshot()
                        recorder.add_node_span(
                            task_id=task_id,
                            name=node_name,
                            started_at=prev_time,
                            duration_ms=(now - prev_time) * 1000,
                            llm_calls=usage.llm_calls - prev_usage.llm_calls,
                            prompt_tokens=usage.prompt_tokens - prev_usage.prompt_tokens,
                            completion_tokens=(
                                usage.completion_tokens - prev_usage.completion_tokens
                            ),
                            cost_usd=usage.cost_usd - prev_usage.cost_usd,
                        )
                        prev_usage = usage
                        prev_time = now
                        logger.info(
                            "AgentService: 节点执行完成",
                            task_id=task_id,
                            node=node_name,
                        )

                        stop_reason = await self._handle_node_event(
                            node_name, node_output, task_id, control
                        )
                        if stop_reason is not None:
                            break

                        # 检查是否有最终结果
                        if node_output.get("final_result"):
                            final_result = node_output["final_result"]

                    if stop_reason is not None:
                        break

                # 任务级用量写入 Trace（含中断/暂停/取消的累计用量）
                usage = budget.snapshot()
                recorder.record_run_usage(
                    task_id=task_id,
                    llm_calls=usage.llm_calls,
                    prompt_tokens=usage.prompt_tokens,
                    completion_tokens=usage.completion_tokens,
                    cost_usd=usage.cost_usd,
                )

            # ---- 依据停止原因收尾 ----
            if stop_reason == "interrupt" and interrupt_payload:
                return await self._persist_approval(
                    task_id, interrupt_payload, caller, recorder
                )

            if stop_reason == "paused":
                logger.info("AgentService: 任务已暂停", task_id=task_id)
                await self.task_service.update_task_status(
                    task_id, TaskStatus.PAUSED
                )
                recorder.finish_run(task_id, status=TaskStatus.PAUSED.value)
                return None

            if stop_reason == "cancelled":
                logger.info("AgentService: 任务已取消", task_id=task_id)
                await self.task_service.update_task_status(
                    task_id, TaskStatus.CANCELLED
                )
                recorder.finish_run(task_id, status=TaskStatus.CANCELLED.value)
                return None

            # 正常收尾：从检查点读取最终状态（断点续跑/已完结线程可能不产出节点事件）
            if final_result is None:
                state = await workflow.aget_state(run_config)
                if state is not None and state.values:
                    final_result = state.values.get("final_result")

            # 执行完成
            status = TaskStatus.COMPLETED if final_result else TaskStatus.FAILED
            await self.task_service.update_task_status(
                task_id, status, final_result=final_result
            )
            recorder.finish_run(task_id, status=status.value)

            # 任务成功时写入长期记忆（归属调用者作用域）
            if final_result:
                await self._remember_result(task_id, goal, final_result, caller)

            logger.info(
                "AgentService: 任务执行完成",
                task_id=task_id,
                status=status.value,
            )

            return final_result

        except BudgetExceededError as e:
            message = f"任务预算超限终止: {e}"
            logger.warning(message, task_id=task_id)
            await self.task_service.update_task_status(
                task_id,
                TaskStatus.FAILED,
                error=message,
            )
            recorder.finish_run(task_id, status=TaskStatus.FAILED.value, error=message)
            return None

        except Exception as e:
            logger.error("AgentService: 任务执行异常", task_id=task_id, error=str(e))
            await self.task_service.update_task_status(
                task_id,
                TaskStatus.FAILED,
                error=str(e),
            )
            recorder.finish_run(task_id, status=TaskStatus.FAILED.value, error=str(e))
            return None

    async def _handle_node_event(
        self,
        node_name: str,
        node_output: dict,
        task_id: str,
        control: TaskControlService,
    ) -> str | None:
        """
        处理单个节点的状态同步，并在节点边界响应暂停/取消请求。

        Returns:
            需要停止时返回停止原因（"paused" / "cancelled"），否则 None。
        """
        if node_name == "supervisor":
            mode = node_output.get("execution_mode")
            if mode:
                await self.task_service.sync_execution_mode(task_id, mode)
            if node_output.get("execution_mode") == "multi_agent":
                await self.task_service.update_task_status(
                    task_id, TaskStatus.EXECUTING
                )
        elif node_name == "sub_agents":
            await self.task_service.update_task_status(
                task_id, TaskStatus.EXECUTING
            )
            await self.task_service.sync_execution_mode(task_id, "multi_agent")
            if node_output.get("agent_results"):
                await self.task_service.sync_agent_results(
                    task_id, node_output["agent_results"]
                )
        elif node_name == "planner":
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

        # 节点边界响应控制请求：取消优先于暂停
        if control.should_cancel(task_id):
            return "cancelled"
        if control.should_pause(task_id):
            return "paused"
        return None

    async def _persist_approval(
        self,
        task_id: str,
        payload: dict,
        caller: ToolContext,
        recorder,
    ) -> None:
        """
        持久化审批请求并置任务为 awaiting_approval。

        审批请求进入任务记录（pending_approval + approval_history），
        前端可查看详情；用户决策后经队列恢复执行。
        """
        requested_at = payload.get("requested_at") or time.time()
        try:
            created_at = datetime.fromtimestamp(
                requested_at, tz=timezone.utc
            ).isoformat()
        except (TypeError, OSError, ValueError):  # pragma: no cover - 防御性
            created_at = _utcnow_iso()

        request = ApprovalRequest(
            id=str(payload.get("approval_id", "")),
            task_id=task_id,
            tool_name=str(payload.get("tool_name", "")),
            args=dict(payload.get("args") or {}),
            reason=str(payload.get("reason", "")),
            created_at=created_at,
        )
        try:
            await self.task_service.save_approval_request(request)
        except Exception as e:  # pragma: no cover - 防御性
            logger.error("AgentService: 持久化审批请求失败", task_id=task_id, error=str(e))

        await self.task_service.update_task_status(
            task_id, TaskStatus.AWAITING_APPROVAL
        )
        recorder.finish_run(
            task_id,
            status=TaskStatus.AWAITING_APPROVAL.value,
            error=f"等待审批: {request.tool_name}",
        )
        logger.warning(
            "AgentService: 任务等待人工审批",
            task_id=task_id,
            approval_id=request.id,
            tool=request.tool_name,
            caller=caller.user_id,
        )
        return None

    # ------------------------------------------------------------------
    # 审批恢复执行
    # ------------------------------------------------------------------

    async def resume_approval(
        self,
        task_id: str,
        decision: dict,
        tool_context: ToolContext | None = None,
    ) -> str | None:
        """审批决策后恢复执行（Worker 消息 action=approval_resume 时调用）。"""
        with structlog.contextvars.bound_contextvars(task_id=task_id):
            return await self._resume_approval_inner(task_id, decision, tool_context)

    async def _resume_approval_inner(
        self,
        task_id: str,
        decision: dict,
        tool_context: ToolContext | None = None,
    ) -> str | None:
        """resume_approval 的实际执行体。"""
        caller = tool_context or ToolContext(role=ROLE_ADMIN)
        task = await self.task_service.get_task(task_id)
        if task is None:
            logger.warning("AgentService: 恢复执行失败，任务不存在", task_id=task_id)
            return None

        recorder = get_trace_recorder()
        if recorder.get_trace(task_id) is None:
            recorder.start_run(
                task_id=task_id,
                goal=task.goal,
                user_id=caller.user_id,
                tenant_id=caller.tenant_id,
            )

        # 以 Command(resume=...) 恢复被 interrupt 暂停的 Workflow
        from langgraph.types import Command

        run_config = {"configurable": {"thread_id": task_id}}
        workflow = await self._get_workflow()
        logger.info(
            "AgentService: 审批决策后恢复执行",
            task_id=task_id,
            decision=decision.get("decision"),
        )
        await self.task_service.update_task_status(task_id, TaskStatus.EXECUTING)

        final_result: str | None = None
        stop_reason: str | None = None
        interrupt_payload: dict[str, Any] | None = None

        try:
            async with budget_scope(
                max_llm_calls=self._settings.MAX_LLM_CALLS_PER_TASK,
                max_total_tokens=self._settings.MAX_TOTAL_TOKENS_PER_TASK,
                budget_limit_usd=self._settings.BUDGET_LIMIT_USD,
                input_cost_per_1m=self._settings.LLM_INPUT_COST_PER_1M,
                output_cost_per_1m=self._settings.LLM_OUTPUT_COST_PER_1M,
            ) as budget:
                control = self._get_control()
                prev_usage = UsageSnapshot()
                prev_time = time.perf_counter()
                async for event in workflow.astream(
                    Command(resume=decision), config=run_config
                ):
                    if "__interrupt__" in event:
                        for item in event["__interrupt__"]:
                            payload = getattr(item, "value", None) or {}
                            is_approval = (
                                isinstance(payload, dict)
                                and payload.get("kind") == APPROVAL_REQUEST_KIND
                            )
                            if is_approval:
                                interrupt_payload = payload
                                stop_reason = "interrupt"
                                break
                        if stop_reason == "interrupt":
                            break

                    for node_name, node_output in event.items():
                        now = time.perf_counter()
                        usage = budget.snapshot()
                        recorder.add_node_span(
                            task_id=task_id,
                            name=node_name,
                            started_at=prev_time,
                            duration_ms=(now - prev_time) * 1000,
                            llm_calls=usage.llm_calls - prev_usage.llm_calls,
                            prompt_tokens=usage.prompt_tokens - prev_usage.prompt_tokens,
                            completion_tokens=(
                                usage.completion_tokens - prev_usage.completion_tokens
                            ),
                            cost_usd=usage.cost_usd - prev_usage.cost_usd,
                        )
                        prev_usage = usage
                        prev_time = now
                        stop_reason = await self._handle_node_event(
                            node_name, node_output, task_id, control
                        )
                        if stop_reason is not None:
                            break
                        if node_output.get("final_result"):
                            final_result = node_output["final_result"]
                    if stop_reason is not None:
                        break

                usage = budget.snapshot()
                recorder.record_run_usage(
                    task_id=task_id,
                    llm_calls=usage.llm_calls,
                    prompt_tokens=usage.prompt_tokens,
                    completion_tokens=usage.completion_tokens,
                    cost_usd=usage.cost_usd,
                )

            if stop_reason == "interrupt" and interrupt_payload:
                return await self._persist_approval(
                    task_id, interrupt_payload, caller, recorder
                )
            if stop_reason == "paused":
                await self.task_service.update_task_status(
                    task_id, TaskStatus.PAUSED
                )
                recorder.finish_run(task_id, status=TaskStatus.PAUSED.value)
                return None
            if stop_reason == "cancelled":
                await self.task_service.update_task_status(
                    task_id, TaskStatus.CANCELLED
                )
                recorder.finish_run(task_id, status=TaskStatus.CANCELLED.value)
                return None

            if final_result is None:
                state = await workflow.aget_state(run_config)
                if state is not None and state.values:
                    final_result = state.values.get("final_result")

            status = TaskStatus.COMPLETED if final_result else TaskStatus.FAILED
            await self.task_service.update_task_status(
                task_id, status, final_result=final_result
            )
            recorder.finish_run(task_id, status=status.value)
            if final_result:
                await self._remember_result(task_id, task.goal, final_result, caller)
            logger.info(
                "AgentService: 恢复执行完成",
                task_id=task_id,
                status=status.value,
            )
            return final_result

        except BudgetExceededError as e:
            message = f"任务预算超限终止: {e}"
            logger.warning(message, task_id=task_id)
            await self.task_service.update_task_status(
                task_id, TaskStatus.FAILED, error=message
            )
            recorder.finish_run(task_id, status=TaskStatus.FAILED.value, error=message)
            return None

        except Exception as e:
            logger.error("AgentService: 恢复执行异常", task_id=task_id, error=str(e))
            await self.task_service.update_task_status(
                task_id, TaskStatus.FAILED, error=str(e)
            )
            recorder.finish_run(task_id, status=TaskStatus.FAILED.value, error=str(e))
            return None
