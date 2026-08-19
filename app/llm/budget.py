"""
LLM 调用预算与用量统计（成本控制 + Trace 数据源）。

每个任务在 AgentService 中进入 budget_scope，此后所有节点内的 LLM 调用
（经 budgeted_ainvoke 发起）都会：
- 调用前检查预算（最大调用次数 / 最大 token 数 / 预算金额），超限抛
  BudgetExceededError，由 AgentService 终止任务并标记 FAILED；
- 调用后通过 UsageCallbackHandler 从 LangChain 回调中捕获
  usage（prompt/completion tokens），累加到任务级 TaskBudget，
  供成本核算与 Agent Trace 节点级用量归因（快照差值）。

设计要点：
- 预算通过 ContextVar 挂载，节点/回调无需显式传递，与 LangGraph 节点
  执行模型解耦；
- 同一预算实例可被并行 Executor 批次的多个并发 LLM 调用共享，
  计数用锁保护；
- 不引入任何外部依赖，纯标准库 + langchain_core 回调接口。
"""

from __future__ import annotations

import threading
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult

from app.config.logging import get_logger

logger = get_logger(__name__)


class BudgetExceededError(RuntimeError):
    """任务 LLM 预算超限，应终止当前任务执行。"""


@dataclass(frozen=True)
class UsageSnapshot:
    """任务/节点的 LLM 用量快照。"""

    llm_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def delta(self, other: "UsageSnapshot") -> "UsageSnapshot":
        """相对 other 快照的增量（用于节点级归因）。"""
        return UsageSnapshot(
            llm_calls=max(0, self.llm_calls - other.llm_calls),
            prompt_tokens=max(0, self.prompt_tokens - other.prompt_tokens),
            completion_tokens=max(0, self.completion_tokens - other.completion_tokens),
            cost_usd=max(0.0, round(self.cost_usd - other.cost_usd, 6)),
        )


class TaskBudget:
    """
    单任务 LLM 预算与用量累计。

    Limits:
        max_llm_calls: 单任务最大 LLM 调用次数（0 表示不限）。
        max_total_tokens: 单任务最大 token 消耗（prompt+completion，0 表示不限）。
        budget_limit_usd: 单任务成本上限（USD，0 表示不限）。
        input_cost_per_1m / output_cost_per_1m: 每百万 token 价格（USD），
            用于成本估算；未配置时成本记为 0。
    """

    def __init__(
        self,
        max_llm_calls: int = 20,
        max_total_tokens: int = 50_000,
        budget_limit_usd: float = 0.0,
        input_cost_per_1m: float = 0.0,
        output_cost_per_1m: float = 0.0,
    ) -> None:
        self._max_llm_calls = max_llm_calls
        self._max_total_tokens = max_total_tokens
        self._budget_limit_usd = budget_limit_usd
        self._input_cost_per_1m = input_cost_per_1m
        self._output_cost_per_1m = output_cost_per_1m
        self._lock = threading.Lock()
        self._llm_calls = 0
        self._prompt_tokens = 0
        self._completion_tokens = 0
        self._cost_usd = 0.0

    # ---- 预算检查 ----

    def check(self) -> None:
        """
        调用 LLM 前检查预算，超限时抛出 BudgetExceededError。

        无预算限制（上限为 0）时静默通过。
        """
        with self._lock:
            if self._max_llm_calls and self._llm_calls >= self._max_llm_calls:
                raise BudgetExceededError(
                    f"任务 LLM 调用次数超限: {self._llm_calls}/{self._max_llm_calls}"
                )
            total = self._prompt_tokens + self._completion_tokens
            if self._max_total_tokens and total >= self._max_total_tokens:
                raise BudgetExceededError(
                    f"任务 token 消耗超限: {total}/{self._max_total_tokens}"
                )
            if self._budget_limit_usd and self._cost_usd >= self._budget_limit_usd:
                raise BudgetExceededError(
                    f"任务成本超限: ${self._cost_usd:.4f}/"
                    f"${self._budget_limit_usd:.4f}"
                )

    def record(
        self, prompt_tokens: int = 0, completion_tokens: int = 0, model: str = ""
    ) -> None:
        """记录一次 LLM 调用消耗；单次调用本身超过 token 上限时立即终止。"""
        with self._lock:
            self._llm_calls += 1
            self._prompt_tokens += max(0, prompt_tokens)
            self._completion_tokens += max(0, completion_tokens)
            self._cost_usd += (
                prompt_tokens * self._input_cost_per_1m
                + completion_tokens * self._output_cost_per_1m
            ) / 1_000_000.0
            total = self._prompt_tokens + self._completion_tokens
            if self._max_total_tokens and total > self._max_total_tokens:
                raise BudgetExceededError(
                    f"任务 token 消耗超限: {total}/{self._max_total_tokens}"
                )

    def snapshot(self) -> UsageSnapshot:
        """返回当前累计用量快照。"""
        with self._lock:
            return UsageSnapshot(
                llm_calls=self._llm_calls,
                prompt_tokens=self._prompt_tokens,
                completion_tokens=self._completion_tokens,
                cost_usd=round(self._cost_usd, 6),
            )


# ---------------------------------------------------------------------------
# ContextVar 挂载：节点与回调共享当前任务的预算实例
# ---------------------------------------------------------------------------

_budget_var: ContextVar[TaskBudget | None] = ContextVar(
    "llm_task_budget", default=None
)


def current_budget() -> TaskBudget | None:
    """返回当前任务预算；无活动预算（如单元测试直调节点）时返回 None。"""
    return _budget_var.get()


@asynccontextmanager
async def budget_scope(
    max_llm_calls: int = 20,
    max_total_tokens: int = 50_000,
    budget_limit_usd: float = 0.0,
    input_cost_per_1m: float = 0.0,
    output_cost_per_1m: float = 0.0,
):
    """
    进入任务预算上下文：内部所有经 budgeted_ainvoke 的 LLM 调用计入该预算。

    用法（AgentService）:
        async with budget_scope(**budget_kwargs) as budget:
            async for event in workflow.astream(...): ...
            snapshot = budget.snapshot()  # 任务级用量
    """
    budget = TaskBudget(
        max_llm_calls=max_llm_calls,
        max_total_tokens=max_total_tokens,
        budget_limit_usd=budget_limit_usd,
        input_cost_per_1m=input_cost_per_1m,
        output_cost_per_1m=output_cost_per_1m,
    )
    token = _budget_var.set(budget)
    try:
        yield budget
    finally:
        _budget_var.reset(token)


# ---------------------------------------------------------------------------
# LangChain 回调：从 LLM 响应中提取 usage 并计入当前预算
# ---------------------------------------------------------------------------


class UsageCallbackHandler(BaseCallbackHandler):
    """
    从 LangChain LLM 响应（LLMResult.llm_output）提取 token 用量并累计。

    兼容不同 Provider 的 usage 结构（Anthropic 的 input/output_tokens、
    OpenAI 风格 token_usage 等），取不到时按 0 处理，不影响调用链。
    """

    def on_llm_end(self, response: LLMResult, **kwargs) -> None:
        budget = current_budget()
        if budget is None:
            return
        llm_output = getattr(response, "llm_output", None) or {}
        usage = llm_output.get("usage") or llm_output.get("token_usage") or {}
        prompt_tokens = usage.get("input_tokens") or usage.get("prompt_tokens") or 0
        completion_tokens = (
            usage.get("output_tokens") or usage.get("completion_tokens") or 0
        )
        try:
            budget.record(
                prompt_tokens=int(prompt_tokens),
                completion_tokens=int(completion_tokens),
                model=llm_output.get("model_name") or "",
            )
        except BudgetExceededError:
            logger.warning("LLM 用量回调触发预算终止", error="token 超限")


# ---------------------------------------------------------------------------
# 统一的受控 LLM 调用入口
# ---------------------------------------------------------------------------


def _runtime_callbacks() -> list[BaseCallbackHandler]:
    """当前预算存在时返回用量统计回调，否则返回空列表。"""
    if current_budget() is None:
        return []
    return [UsageCallbackHandler()]


async def budgeted_ainvoke(runnable, input=None, **kwargs):
    """
    在预算上下文内调用任意 Runnable（LLM / LCEL 链）。

    - 调用前 check()：次数/token/金额超限时抛 BudgetExceededError；
    - 注入 UsageCallbackHandler，调用结束后自动累计用量。
    - 无活动预算时等价于 runnable.ainvoke(input)，行为完全兼容。

    Args:
        runnable: LangChain Runnable（ChatModel / prompt|llm|parser 链）。
        input: 调用输入。
        **kwargs: 透传给 runnable.ainvoke 的额外参数（config 会被合并）。

    Returns:
        runnable.ainvoke 的返回值。

    Raises:
        BudgetExceededError: 预算超限（仅活动预算下）。
    """
    budget = current_budget()
    if budget is None:
        return await runnable.ainvoke(input, **kwargs)
    budget.check()
    config = kwargs.pop("config", None) or {}
    callbacks = config.get("callbacks", []) + _runtime_callbacks()
    config["callbacks"] = callbacks
    return await runnable.ainvoke(input, config=config, **kwargs)
