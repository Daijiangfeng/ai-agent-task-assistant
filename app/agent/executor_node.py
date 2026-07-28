"""
Executor Agent 节点。
负责按计划依次执行子任务，调用工具获取结果。
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from typing import Any, Optional

from langchain_core.messages import AIMessage

from app.agent.state import AgentState
from app.config.logging import get_logger
from app.llm.base import BaseLLMProvider
from app.prompts.manager import PromptManager
from app.tools.registry import ToolRegistry

logger = get_logger(__name__)

# 审批钩子类型：接收 (工具名, 工具参数)，返回是否放行执行。
ApprovalHook = Callable[[str, dict[str, Any]], bool]


class ToolExecutionPolicy:
    """
    智能体层工具执行边界（最小化）。

    职责：
    - 显式白名单：仅允许已登记（注册在案）的工具被调用，未登记工具名默认拒绝。
    - 副作用工具审批：sql_query / file_processing / web_search 在执行前经过
      一个"可拒绝"的审批钩子，钩子返回 False 或抛异常时拒绝执行。
    - 不改变各工具内部既有的安全校验，仅在调用链上追加一层拦截。
    """

    # 有副作用的工具，执行前必须通过审批钩子。
    SIDE_EFFECT_TOOLS: frozenset[str] = frozenset(
        {"sql_query", "file_processing", "web_search"}
    )

    def __init__(
        self,
        allowed_tools: Iterable[str],
        approval_hook: Optional[ApprovalHook] = None,
    ) -> None:
        """
        Args:
            allowed_tools: 允许调用的工具名白名单（通常为已注册工具集合）。
            approval_hook: 副作用工具的审批钩子；缺省放行，可注入以实现拒绝。
        """
        self._allowed: set[str] = set(allowed_tools)
        # 默认放行，但保留可注入的"可拒绝"钩子。
        self._approval_hook: ApprovalHook = approval_hook or (lambda name, args: True)

    def check(self, tool_name: str, tool_args: dict[str, Any]) -> tuple[bool, Optional[str]]:
        """
        校验某次工具调用是否允许执行。

        Args:
            tool_name: LLM 请求调用的工具名。
            tool_args: 工具调用参数。

        Returns:
            (allowed, reason)。allowed 为 False 时 reason 说明拒绝原因。
        """
        if tool_name not in self._allowed:
            return False, f"工具未登记在白名单中，默认拒绝: {tool_name}"

        if tool_name in self.SIDE_EFFECT_TOOLS:
            try:
                approved = self._approval_hook(tool_name, tool_args)
            except Exception as e:  # 审批钩子异常按拒绝处理，避免默认放行
                return False, f"副作用工具审批钩子异常，拒绝执行 {tool_name}: {e}"
            if not approved:
                return False, f"副作用工具审批被拒绝: {tool_name}"

        return True, None


class ExecutorNode:
    """
    Executor Agent 节点。

    职责：
    - 按计划依次执行子任务
    - 使用 LLM + 工具完成每个子任务
    - 将结果追加到 task_results
    """

    def __init__(
        self,
        llm_provider: BaseLLMProvider,
        prompt_manager: PromptManager,
        tool_approval_hook: Optional[ApprovalHook] = None,
    ):
        self.llm = llm_provider.get_chat_model()
        self.prompt_manager = prompt_manager
        # 副作用工具的审批钩子，缺省放行；可由上层注入以实现拒绝。
        self._tool_approval_hook = tool_approval_hook

    async def run(self, state: AgentState) -> dict[str, Any]:
        """
        执行当前子任务（支持并行执行无依赖的子任务）。

        如果计划中的子任务包含 depends_on 字段，将按依赖拓扑分层执行：
        同层内的子任务并行执行，跨层串行执行。若无 depends_on 字段，
        退化为原有的逐个串行执行模式。

        Args:
            state: 当前 Agent 状态。

        Returns:
            状态更新字典。
        """
        plan = state["plan"]
        idx = state["current_task_index"]

        if not plan or "subtasks" not in plan:
            return {
                "final_result": "无有效执行计划。",
                "errors": ["Executor: 无有效计划"],
            }

        subtasks = plan["subtasks"]
        if idx >= len(subtasks):
            logger.info("Executor: 所有子任务已完成")
            return {
                "messages": [AIMessage(content="All subtasks completed")],
                "errors": [],
            }

        # Check if this is a parallelizable batch
        parallel_batch = self._get_parallel_batch(subtasks, idx, state)

        if len(parallel_batch) > 1:
            # Parallel execution of independent subtasks
            return await self._run_parallel_batch(state, subtasks, parallel_batch)

        # Single task execution (original path)
        subtask = subtasks[idx]
        logger.info(
            "Executor: 执行子任务",
            index=idx,
            total=len(subtasks),
            task_id=subtask.get("id", "unknown"),
        )

        # 构建之前任务的结果上下文
        previous_results = self._build_previous_context(state["task_results"])

        # 获取可用工具
        tools = ToolRegistry.get_all_langchain_tools()
        t0 = time.perf_counter()

        try:
            # 构造执行消息
            prompt = self.prompt_manager.get_executor_prompt()
            messages = prompt.format_messages(
                previous_results=previous_results or "无",
                subtask_description=subtask["description"],
            )

            if tools:
                # 有工具时绑定 LLM
                llm_with_tools = self.llm.bind_tools(tools)
                response = await llm_with_tools.ainvoke(messages)

                # 处理工具调用
                if response.tool_calls:
                    result_content = await self._execute_tool_calls(
                        response, tools, messages, llm_with_tools
                    )
                else:
                    result_content = response.content
            else:
                # 无工具时直接调用 LLM
                response = await self.llm.ainvoke(messages)
                result_content = response.content

            # 构造任务结果
            exec_ms = (time.perf_counter() - t0) * 1000
            task_result = {
                "subtask_id": subtask.get("id", f"task_{idx}"),
                "description": subtask["description"],
                "result": result_content,
                "status": "completed",
                "latency_ms": round(exec_ms, 1),
            }

            logger.info(
                "Executor: 子任务完成",
                index=idx,
                task_id=task_result["subtask_id"],
                latency_ms=round(exec_ms, 1),
            )

            return {
                "current_task_index": idx + 1,
                "task_results": [task_result],
                "messages": [
                    AIMessage(
                        content=f"Completed subtask {idx + 1}/{len(subtasks)}: "
                        f"{subtask.get('id', 'unknown')}"
                    )
                ],
                "errors": [],
            }

        except Exception as e:
            logger.error("Executor: 子任务执行失败", error=str(e), index=idx)

            task_result = {
                "subtask_id": subtask.get("id", f"task_{idx}"),
                "description": subtask["description"],
                "result": None,
                "status": "failed",
                "error": str(e),
            }

            return {
                "current_task_index": idx + 1,
                "task_results": [task_result],
                "messages": [],
                "errors": [f"Executor subtask {idx} failed: {str(e)}"],
            }

    async def _execute_tool_calls(
        self, response, tools: list, messages: list, llm_with_tools
    ) -> str:
        """处理工具调用链。

        在此追加智能体层执行边界：未登记工具名默认拒绝，副作用工具须经审批钩子放行。
        被拒绝的调用不会触达工具实现，仅记录原因并回填一条拒绝说明给 LLM。
        """
        from langchain_core.messages import ToolMessage

        tool_map = {t.name: t for t in tools}
        # 白名单即当前已登记（注册在案）的工具集合。
        policy = ToolExecutionPolicy(
            allowed_tools=tool_map.keys(),
            approval_hook=self._tool_approval_hook,
        )
        all_messages = list(messages) + [response]

        for tool_call in response.tool_calls:
            tool_name = tool_call["name"]
            tool_args = tool_call["args"]

            allowed, reason = policy.check(tool_name, tool_args)
            if not allowed:
                logger.warning(
                    "Executor: 工具调用被执行边界拒绝",
                    tool=tool_name,
                    reason=reason,
                )
                all_messages.append(
                    ToolMessage(
                        content=f"工具调用被拒绝: {reason}",
                        tool_call_id=tool_call["id"],
                    )
                )
                continue

            try:
                tool_result = await self._invoke_with_retry(
                    tool_map[tool_name], tool_args
                )
                all_messages.append(
                    ToolMessage(content=str(tool_result), tool_call_id=tool_call["id"])
                )
            except Exception as e:
                all_messages.append(
                    ToolMessage(
                        content=f"工具执行失败: {str(e)}",
                        tool_call_id=tool_call["id"],
                    )
                )

        # 让 LLM 根据工具结果生成最终回复
        final_response = await llm_with_tools.ainvoke(all_messages)
        return final_response.content

    async def _invoke_with_retry(
        self, tool, tool_args: dict[str, Any], max_retries: int = 2
    ) -> Any:
        """调用工具并对瞬时错误进行有限重试。

        对网络超时、连接错误等瞬时性异常自动重试最多 max_retries 次，
        使用指数退避（1s, 2s）。非瞬时错误（如参数错误）直接抛出。
        """
        import asyncio

        last_exc: Exception | None = None
        for attempt in range(max_retries + 1):
            try:
                return await tool.ainvoke(tool_args)
            except Exception as e:
                error_str = str(e).lower()
                # 判断是否为可重试的瞬时错误
                is_transient = any(
                    keyword in error_str
                    for keyword in ("timeout", "connection", "temporary", "unavailable", "429")
                )
                if not is_transient or attempt >= max_retries:
                    raise
                last_exc = e
                delay = 1.0 * (2 ** attempt)
                logger.warning(
                    "Executor: 工具调用瞬时失败，%0.1fs后重试",
                    delay,
                    tool=tool.name if hasattr(tool, 'name') else '?',
                    attempt=attempt + 1,
                    error=str(e),
                )
                await asyncio.sleep(delay)
        raise last_exc  # type: ignore[misc]

    def _build_previous_context(self, task_results: list[dict]) -> str:
        """构建之前任务结果的上下文文本。"""
        if not task_results:
            return ""

        lines = []
        for r in task_results:
            status = r.get("status", "unknown")
            result = r.get("result", "无结果")
            lines.append(
                f"[{r.get('subtask_id', '?')}] ({status}): "
                f"{r.get('description', '')} -> {result}"
            )
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Parallel execution support (Section 1.4 / 2.1)
    # ------------------------------------------------------------------

    def _get_parallel_batch(
        self, subtasks: list[dict], current_idx: int, state: AgentState
    ) -> list[int]:
        """识别从 current_idx 开始的可并行执行批次。

        如果子任务不包含 depends_on 字段，返回单个索引。
        如果包含，找出从 current_idx 开始所有已满足依赖的子任务。
        """
        completed_ids = {
            r.get("subtask_id")
            for r in state.get("task_results", [])
            if r.get("status") == "completed"
        }

        # If no subtask has depends_on, fall back to serial mode
        has_deps = any("depends_on" in t for t in subtasks)
        if not has_deps:
            return [current_idx]

        # Find all subtasks that are ready to execute (dependencies met)
        batch = []
        for i in range(current_idx, len(subtasks)):
            task = subtasks[i]
            task_id = task.get("id", f"task_{i}")
            if task_id in completed_ids:
                continue
            deps = task.get("depends_on", [])
            if all(d in completed_ids for d in deps):
                batch.append(i)

        return batch if batch else [current_idx]

    async def _run_parallel_batch(
        self, state: AgentState, subtasks: list[dict], batch_indices: list[int]
    ) -> dict[str, Any]:
        """并行执行一批无依赖的子任务。"""
        import asyncio

        logger.info(
            "Executor: 并行执行 %d 个无依赖子任务",
            len(batch_indices),
            task_ids=[subtasks[i].get("id", f"task_{i}") for i in batch_indices],
        )

        async def _exec_one(task_idx: int) -> dict[str, Any]:
            subtask = subtasks[task_idx]
            previous_results = self._build_previous_context(state["task_results"])
            tools = ToolRegistry.get_all_langchain_tools()
            t0 = time.perf_counter()

            try:
                prompt = self.prompt_manager.get_executor_prompt()
                messages = prompt.format_messages(
                    previous_results=previous_results or "无",
                    subtask_description=subtask["description"],
                )
                if tools:
                    llm_with_tools = self.llm.bind_tools(tools)
                    response = await llm_with_tools.ainvoke(messages)
                    if response.tool_calls:
                        result_content = await self._execute_tool_calls(
                            response, tools, messages, llm_with_tools
                        )
                    else:
                        result_content = response.content
                else:
                    response = await self.llm.ainvoke(messages)
                    result_content = response.content

                exec_ms = (time.perf_counter() - t0) * 1000
                return {
                    "subtask_id": subtask.get("id", f"task_{task_idx}"),
                    "description": subtask["description"],
                    "result": result_content,
                    "status": "completed",
                    "latency_ms": round(exec_ms, 1),
                }
            except Exception as e:
                exec_ms = (time.perf_counter() - t0) * 1000
                return {
                    "subtask_id": subtask.get("id", f"task_{task_idx}"),
                    "description": subtask["description"],
                    "result": f"执行失败: {str(e)}",
                    "status": "failed",
                    "latency_ms": round(exec_ms, 1),
                }

        # Execute all tasks in the batch concurrently
        results = await asyncio.gather(*[_exec_one(i) for i in batch_indices])

        # Advance index past all completed batch tasks
        new_idx = max(batch_indices) + 1

        logger.info(
            "Executor: 并行批次完成",
            count=len(results),
            next_idx=new_idx,
        )

        return {
            "task_results": list(results),
            "current_task_index": new_idx,
            "messages": [AIMessage(content=f"Parallel batch completed: {len(results)} subtasks")],
            "errors": [],
        }
