"""
LLM 成本控制（预算）测试。
覆盖 TaskBudget 限额、UsageCallbackHandler 用量统计、budgeted_ainvoke
受控调用，以及预算超限时 Agent 任务整体终止（FAILED + 预算错误信息）。
"""

import asyncio
from unittest.mock import MagicMock

import pytest
from langchain_core.outputs import LLMResult
from langchain_core.runnables import Runnable

from app.config.settings import Settings
from app.llm.budget import (
    BudgetExceededError,
    TaskBudget,
    UsageCallbackHandler,
    budget_scope,
    budgeted_ainvoke,
    current_budget,
)
from app.models.task import TaskStatus


class TestTaskBudget:
    """TaskBudget 限额与累计逻辑。"""

    def test_default_limits(self):
        """默认 20 次调用 / 50k tokens，且不限额时 check 放行。"""
        budget = TaskBudget()
        for _ in range(100):
            budget.check()
        assert budget.snapshot().llm_calls == 0

    def test_llm_calls_limit(self):
        """调用次数超限时 check 抛 BudgetExceededError。"""
        budget = TaskBudget(max_llm_calls=2)
        budget.record(prompt_tokens=1)
        budget.record(prompt_tokens=1)
        with pytest.raises(BudgetExceededError, match="次数超限"):
            budget.check()

    def test_total_tokens_limit_checked_before_call(self):
        """累计 token 达上限后，下一次调用前 check 即抛错。"""
        budget = TaskBudget(max_total_tokens=100)
        budget.record(prompt_tokens=50, completion_tokens=50)
        with pytest.raises(BudgetExceededError, match="token 消耗超限"):
            budget.check()

    def test_single_call_exceeding_limit_terminates(self):
        """单次调用消耗超过上限时 record 立即抛错（终止任务）。"""
        budget = TaskBudget(max_total_tokens=100)
        with pytest.raises(BudgetExceededError):
            budget.record(prompt_tokens=200)

    def test_cost_limit(self):
        """成本超限时 check 抛错。"""
        budget = TaskBudget(
            budget_limit_usd=0.01, input_cost_per_1m=1.0, output_cost_per_1m=1.0
        )
        budget.record(prompt_tokens=10_000, completion_tokens=10_000)  # $0.02
        with pytest.raises(BudgetExceededError, match="成本超限"):
            budget.check()

    def test_snapshot_and_cost_estimate(self):
        """快照含调用次数/token/成本，成本按每百万 token 单价估算。"""
        budget = TaskBudget(input_cost_per_1m=0.5, output_cost_per_1m=1.5)
        budget.record(prompt_tokens=1000, completion_tokens=500)
        snap = budget.snapshot()
        assert snap.llm_calls == 1
        assert snap.prompt_tokens == 1000
        assert snap.completion_tokens == 500
        assert snap.total_tokens == 1500
        # 1000*0.5 + 500*1.5 = 500 + 750 = 1250 每百万 → $0.00125
        assert snap.cost_usd == pytest.approx(0.00125)
        # 节点归因：增量快照
        budget.record(prompt_tokens=100, completion_tokens=100)
        delta = budget.snapshot().delta(snap)
        assert delta.llm_calls == 1
        assert delta.prompt_tokens == 100


class TestBudgetScope:
    """budget_scope 上下文与 ContextVar 挂载。"""

    @pytest.mark.asyncio
    async def test_scope_sets_and_restores_budget(self):
        """scope 内 current_budget() 可用，退出后恢复 None。"""
        assert current_budget() is None
        async with budget_scope(max_llm_calls=5) as budget:
            assert current_budget() is budget
            assert budget is not None
        assert current_budget() is None


class _FakeRunnable:
    """模拟 LangChain 行为：注入的回调会被触发（记录用量）。"""

    def __init__(self, usage: dict | None = None):
        self.usage = usage or {"input_tokens": 10, "output_tokens": 5}
        self.last_config = None

    async def ainvoke(self, input=None, config=None, **kwargs):
        self.last_config = config
        for cb in ((config or {}).get("callbacks") or []):
            result = LLMResult(generations=[[]])
            result.llm_output = {"model_name": "glm-test", "usage": self.usage}
            cb.on_llm_end(result)
        return {"content": "ok"}


def _llm_result_with_usage(usage: dict) -> LLMResult:
    """构造携带 usage 的 LLMResult（模拟 ChatAnthropic 的 llm_output）。"""
    result = LLMResult(generations=[[]])
    result.llm_output = {"model_name": "glm-4.5-air", "usage": usage}
    return result


class TestUsageCallbackHandler:
    """用量回调：从 LLMResult 提取 usage 并计入预算。"""

    def test_on_llm_end_records_usage(self):
        """回调将 input/output tokens 计入当前预算。"""
        handler = UsageCallbackHandler()

        async def run():
            async with budget_scope() as budget:
                handler.on_llm_end(
                    _llm_result_with_usage({"input_tokens": 10, "output_tokens": 5})
                )
                return budget.snapshot()

        snap = asyncio.run(run())
        assert snap.llm_calls == 1
        assert snap.prompt_tokens == 10
        assert snap.completion_tokens == 5

    def test_on_llm_end_supports_openai_style_usage(self):
        """兼容 OpenAI 风格 token_usage 结构。"""
        handler = UsageCallbackHandler()
        result = LLMResult(generations=[[]])
        result.llm_output = {"token_usage": {"prompt_tokens": 7, "completion_tokens": 3}}

        async def run():
            async with budget_scope() as budget:
                handler.on_llm_end(result)
                return budget.snapshot()

        snap = asyncio.run(run())
        assert snap.prompt_tokens == 7
        assert snap.completion_tokens == 3

    def test_no_budget_noop(self):
        """无活动预算时回调为空操作（直调节点场景）。"""
        UsageCallbackHandler().on_llm_end(_llm_result_with_usage({"input_tokens": 1}))
        assert current_budget() is None


class TestBudgetedAinvoke:
    """受控 LLM 调用入口：前置检查 + 回调注入。"""

    @pytest.mark.asyncio
    async def test_injects_callback_and_records_usage(self):
        """预算内调用：注入回调、记录用量、config 透传。"""
        runnable = _FakeRunnable()

        async def run():
            async with budget_scope() as budget:
                await budgeted_ainvoke(runnable, {"q": 1})
                return budget.snapshot()

        snap = await run()
        assert snap.llm_calls == 1
        assert runnable.last_config is not None
        handlers = runnable.last_config.get("callbacks", [])
        assert any(isinstance(h, UsageCallbackHandler) for h in handlers)

    @pytest.mark.asyncio
    async def test_raises_when_budget_exceeded(self):
        """预算耗尽后下一次调用直接抛 BudgetExceededError（不发起调用）。"""
        runnable = _FakeRunnable()

        async def run():
            async with budget_scope(max_llm_calls=1):
                await budgeted_ainvoke(runnable, {"q": 1})
                with pytest.raises(BudgetExceededError):
                    await budgeted_ainvoke(runnable, {"q": 2})

        await run()
        assert runnable.last_config is not None

    @pytest.mark.asyncio
    async def test_no_budget_passthrough(self):
        """无活动预算时等价于裸 ainvoke（config 为 None）。"""
        runnable = _FakeRunnable()
        result = await budgeted_ainvoke(runnable, {"q": 1})
        assert result == {"content": "ok"}
        assert runnable.last_config is None


class FakeBudgetChatModel(Runnable):
    """
    确定性假 ChatModel（可挂入 prompt|llm|parser 链）。

    - 返回固定 AIMessage（内容为 JSON，兼容 JsonOutputParser）；
    - 触发 config 中注入的回调（on_llm_end + usage），使预算统计生效。
    """

    def __init__(self, response: str, usage: dict | None = None):
        from langchain_core.messages import AIMessage

        self._response = AIMessage(content=response)
        self._usage = usage or {"input_tokens": 10, "output_tokens": 5}

    def bind_tools(self, tools, **kwargs) -> "FakeBudgetChatModel":
        return self

    def invoke(self, model_input, config=None, **kwargs):  # pragma: no cover - 异步链路
        import asyncio

        return asyncio.run(self.ainvoke(model_input, config=config, **kwargs))

    async def ainvoke(self, model_input, config=None, **kwargs):
        # 直连模型时 callbacks 是 handler 列表；经 prompt|llm|parser 链调用时
        # LangChain 已包装为 AsyncCallbackManager（handlers 属性取真实列表），
        # 两种形态都触发用量回调。
        callbacks = (config or {}).get("callbacks")
        if callbacks is not None:
            result = LLMResult(generations=[[]])
            result.llm_output = {"model_name": "glm-test", "usage": self._usage}
            if hasattr(callbacks, "handlers"):
                callbacks = callbacks.handlers
            for cb in callbacks:
                cb.on_llm_end(result)
        return self._response


def make_fake_provider(response_json: str, usage: dict | None = None):
    """构造返回固定 JSON 的假 Provider（离线跑完整 Workflow）。"""
    provider = MagicMock()
    provider.get_chat_model = MagicMock(
        return_value=FakeBudgetChatModel(response_json, usage)
    )
    provider.get_client = MagicMock(return_value=MagicMock())
    return provider


PLAN_JSON = (
    '{"goal": "test", "subtasks": [{"id": "task_1", '
    '"description": "test task", "dependencies": [], "tool": null}]}'
)


class TestAgentBudgetIntegration:
    """预算超限时完整 Agent 流程终止为 FAILED。"""

    def _build_env(self, monkeypatch, provider, **settings_kwargs):
        """构造离线可跑的 AgentService + TaskService。"""
        import app.services.agent_service as agent_service_module
        from app.prompts.manager import PromptManager
        from app.services.agent_service import AgentService
        from app.services.task_service import TaskService

        class _FakeFactory:
            @staticmethod
            def create(*args, **kwargs):
                return provider

        monkeypatch.setattr(agent_service_module, "LLMProviderFactory", _FakeFactory)
        PromptManager.init_defaults()

        task_service = TaskService(Settings(TASK_STORAGE_BACKEND="memory"))
        settings = Settings(
            TASK_STORAGE_BACKEND="memory",
            CHECKPOINT_BACKEND="memory",
            MAX_REPLAN_ITERATIONS=2,
            **settings_kwargs,
        )
        service = AgentService(task_service=task_service, settings=settings)
        return service, task_service

    @pytest.mark.asyncio
    async def test_task_fails_gracefully_when_budget_exhausted(self, monkeypatch):
        """
        单任务预算只允许 1 次 LLM 调用：Planner 消耗后，Executor 的
        调用前置检查抛 BudgetExceededError，任务标记 FAILED 且错误
        信息包含预算原因（而非崩溃）。
        """
        from app.models.task import TaskStatus

        service, task_service = self._build_env(
            monkeypatch,
            make_fake_provider(PLAN_JSON),
            MAX_LLM_CALLS_PER_TASK=1,
            MAX_TOTAL_TOKENS_PER_TASK=0,
        )

        task_id = await task_service.create_task("预算测试任务")
        final_result = await service.run_task(task_id, "预算测试任务")

        assert final_result is None
        task = await task_service.get_task(task_id)
        assert task.status == TaskStatus.FAILED
        assert task.error and "预算" in task.error

    @pytest.mark.asyncio
    async def test_task_succeeds_within_budget(self, monkeypatch):
        """预算充足时任务正常完成。"""
        service, task_service = self._build_env(
            monkeypatch,
            make_fake_provider(PLAN_JSON),
            MAX_LLM_CALLS_PER_TASK=100,
            MAX_TOTAL_TOKENS_PER_TASK=1_000_000,
        )

        task_id = await task_service.create_task("预算充足任务")
        final_result = await service.run_task(task_id, "预算充足任务")

        assert final_result is not None
        task = await task_service.get_task(task_id)
        assert task.status == TaskStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_trace_records_budget_termination(self, monkeypatch):
        """预算终止的任务在 Trace 中记录为 failed 且带错误信息。"""
        from app.tracing.recorder import get_trace_recorder

        service, task_service = self._build_env(
            monkeypatch,
            make_fake_provider(PLAN_JSON),
            MAX_LLM_CALLS_PER_TASK=1,
            MAX_TOTAL_TOKENS_PER_TASK=0,
        )

        task_id = await task_service.create_task("预算终止追踪")
        await service.run_task(task_id, "预算终止追踪")

        trace = get_trace_recorder().get_trace(task_id)
        assert trace is not None
        assert trace.status == "failed"
        assert trace.error and "预算" in trace.error
