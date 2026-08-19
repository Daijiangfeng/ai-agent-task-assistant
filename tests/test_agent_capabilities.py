"""
Agent 能力回归测试（Case 6~10）。

覆盖：
- Case 6: Multi-Agent Context 全链路透传
  （Supervisor / Research / Data / Writing / Reviewer 均能访问原始需求与已提取参数，
   不得再声称缺少目的地、时间、预算）；
- Case 7: Required Parameter Blocking
  （缺参数时确定性阻断工具调用，web_search 不得执行，Agent 必须询问地点）；
- Case 8: web_search 默认不触发 HITL（L0 只读，AUTO；风险分级可配置）；
- Case 9: Tool Failure
  （不崩溃、有限重试、重试失败进入明确失败状态、最终回答不编造结果）；
- Case 10: Reviewer Context
  （Reviewer 同时看到原始需求 + 各 Agent 产出 + 工具结果）。
"""

import json
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage
from langchain_core.runnables import Runnable

from app.agent.approval import HumanApprovalGate
from app.agent.executor_node import run_tool_calls_loop
from app.agent.multi_agent import (
    MODE_MULTI_AGENT,
    ReviewerNode,
    SubAgentsNode,
    SupervisorNode,
)
from app.agent.requirements import (
    check_tool_requirements,
    extract_requirements,
)
from app.prompts.manager import PromptManager

TRAVEL_QUERY = (
    "我想制定一个周末旅行计划。"
    "去台北，周六早上出发，周日晚上回来，预算1000元。"
)
RESTAURANT_QUERY = "帮我找一家明天晚上吃饭的餐厅，两个人，预算300元。"

SUPERVISOR_MULTI_JSON = json.dumps(
    {
        "mode": "multi_agent",
        "agents": [
            {"role": "research", "objective": "收集台北旅行信息"},
            {"role": "data", "objective": "整理行程数据与预算"},
            {"role": "writing", "objective": "撰写完整旅行计划"},
        ],
        "reasoning": "需要搜索资料、整理数据并撰写报告，跨领域协作",
    },
    ensure_ascii=False,
)


class _FakeModel(Runnable):
    """按调用顺序返回固定响应的假模型（含 bind_tools / Runnable 兼容）。"""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    def bind_tools(self, tools, **kwargs):
        return self

    def invoke(self, model_input, config=None, **kwargs):  # pragma: no cover
        import asyncio

        return asyncio.run(self.ainvoke(model_input, config=config, **kwargs))

    async def ainvoke(self, model_input, config=None, **kwargs):
        self.calls += 1
        response = self._responses.pop(0) if self._responses else "默认回复"
        return AIMessage(content=response)


class _CapturingModel:
    """记录每次调用输入文本的假模型。"""

    def __init__(self, response="完成"):
        self.seen_inputs: list[str] = []
        self._response = response

    def bind_tools(self, tools, **kwargs):
        return self

    async def ainvoke(self, model_input, config=None, **kwargs):
        self.seen_inputs.append(str(model_input))
        return AIMessage(content=self._response)


class _RecordingTool:
    """记录是否被调用的假工具（用于验证工具未执行）。"""

    def __init__(self, name: str):
        self.name = name
        self.calls = 0

    async def ainvoke(self, args):
        self.calls += 1
        return "搜索结果"


class _FailingTool:
    """总是抛异常的假工具（用于验证失败回退）。"""

    def __init__(self, name: str, message: str = "模拟工具故障"):
        self.name = name
        self.calls = 0
        self._message = message

    async def ainvoke(self, args):
        self.calls += 1
        raise RuntimeError(self._message)


def _make_provider(model) -> MagicMock:
    provider = MagicMock()
    provider.get_chat_model = MagicMock(return_value=model)
    provider.get_client = MagicMock(return_value=MagicMock())
    return provider


def _make_state(**overrides) -> dict:
    state = {
        "goal": "调研 AI 行业并撰写报告",
        "context": None,
        "original_user_query": None,
        "conversation_history": [],
        "extracted_requirements": None,
        "missing_requirements": [],
        "intermediate_results": [],
        "tool_results": [],
        "subagent_results": [],
        "tool_context": None,
        "task_id": "task-cap-1",
        "execution_mode": None,
        "agent_assignments": [],
        "agent_results": [],
        "plan": None,
        "plan_version": 0,
        "current_task_index": 0,
        "task_results": [],
        "retry_from_index": None,
        "reflection_result": None,
        "should_replan": False,
        "iteration_count": 0,
        "final_result": None,
        "messages": [],
        "errors": [],
    }
    state.update(overrides)
    return state


@pytest.fixture(scope="module")
def prompts():
    PromptManager.init_defaults()


class TestCase6MultiAgentContext:
    """Case 6：Multi-Agent Context 全链路透传。"""

    def test_requirement_extraction(self):
        """从旅行需求中确定性提取目的地/出发/返回/预算。"""
        extracted = extract_requirements(TRAVEL_QUERY)
        assert extracted["destination"] == "台北"
        assert extracted["departure_time"] == "周六早上"
        assert extracted["return_time"] == "周日晚上"
        assert extracted["budget"] == "1000"

    @pytest.mark.asyncio
    async def test_supervisor_preserves_query_and_requirements(self, prompts):
        """Supervisor 拿到完整 Query，并回写已提取参数。"""
        node = SupervisorNode(
            _make_provider(_FakeModel([SUPERVISOR_MULTI_JSON])), PromptManager
        )
        state = _make_state(
            goal=TRAVEL_QUERY,
            original_user_query=TRAVEL_QUERY,
            conversation_history=[{"role": "user", "content": TRAVEL_QUERY}],
        )
        result = await node.run(state)
        assert result["execution_mode"] == MODE_MULTI_AGENT
        assert result["extracted_requirements"]["destination"] == "台北"
        assert result["extracted_requirements"]["budget"] == "1000"

    @pytest.mark.asyncio
    async def test_every_sub_agent_receives_full_context(self, prompts):
        """Research / Data / Writing 均注入原始需求与已提取参数，不得声称缺失。"""
        model = _CapturingModel()
        node = SubAgentsNode(_make_provider(model), PromptManager)
        state = _make_state(
            execution_mode=MODE_MULTI_AGENT,
            original_user_query=TRAVEL_QUERY,
            conversation_history=[{"role": "user", "content": TRAVEL_QUERY}],
            extracted_requirements=extract_requirements(TRAVEL_QUERY),
            agent_assignments=[
                {"role": "research", "objective": "收集台北旅行信息"},
                {"role": "data", "objective": "整理行程数据与预算"},
                {"role": "writing", "objective": "撰写完整旅行计划"},
            ],
        )
        result = await node.run(state)
        assert len(model.seen_inputs) == 3
        assert [r["role"] for r in result["agent_results"]] == [
            "research",
            "data",
            "writing",
        ]
        for text in model.seen_inputs:
            # 原始需求完整透传
            assert "去台北" in text
            assert "周六早上" in text
            assert "周日晚上" in text
            assert "1000" in text
            # 已提取参数结构化透传
            assert "destination" in text
            assert "departure_time" in text
            assert "return_time" in text
            assert "budget" in text
            # 不得声称缺少目的地/时间/预算
            assert "缺少目的地" not in text
            assert "缺少目的地、时间、预算" not in text

    @pytest.mark.asyncio
    async def test_reviewer_receives_full_context(self, prompts):
        """Reviewer 同时看到原始需求、已提取参数与全部 Agent 产出。"""
        model = _CapturingModel("最终报告：整合完成")
        node = ReviewerNode(_make_provider(model), PromptManager)
        state = _make_state(
            execution_mode=MODE_MULTI_AGENT,
            original_user_query=TRAVEL_QUERY,
            extracted_requirements=extract_requirements(TRAVEL_QUERY),
            agent_results=[
                {
                    "role": "research",
                    "agent_name": "Research Agent",
                    "result": "台北热门景点：故宫、101",
                    "status": "completed",
                },
                {
                    "role": "data",
                    "agent_name": "Data Agent",
                    "result": "预算 1000 元可覆盖交通与门票",
                    "status": "completed",
                },
                {
                    "role": "writing",
                    "agent_name": "Writing Agent",
                    "result": "已撰写行程初稿",
                    "status": "completed",
                },
            ],
        )
        result = await node.run(state)
        text = model.seen_inputs[0]
        assert "去台北" in text
        assert "destination" in text
        assert "故宫" in text
        assert "预算 1000 元" in text
        assert "已撰写行程初稿" in text
        assert result["final_result"] == "最终报告：整合完成"


class TestCase7RequiredParameterBlocking:
    """Case 7：缺参数时确定性阻断工具调用。"""

    def test_location_detected_missing(self):
        """餐厅查询提取出日期/时间/人数/预算，但 location 缺失。"""
        extracted = extract_requirements(RESTAURANT_QUERY)
        assert "location" not in extracted
        assert extracted.get("date") == "明天"
        assert extracted.get("time") == "晚上"
        assert extracted.get("budget") == "300"

    def test_check_tool_requirements_blocks(self):
        """web_search 在 location 缺失时被确定性阻断。"""
        result = check_tool_requirements(
            "web_search", {"query": RESTAURANT_QUERY}
        )
        assert result.allowed is False
        assert "location" in result.missing
        assert "城市/区域" in result.labels
        assert "请问" in result.question

    def test_allowed_when_location_provided(self):
        """位置可提取或已提取时放行（通用机制，非针对餐厅硬编码）。"""
        # 位置在查询中可确定性提取（"北京有哪些..."）
        result = check_tool_requirements(
            "web_search",
            {"query": "帮我查一下北京有哪些热门景点"},
        )
        assert result.allowed is True
        # 位置已存在于已提取参数中（对话历史提供过）
        result = check_tool_requirements(
            "web_search",
            {"query": "帮我找一家明天晚上吃饭的餐厅，两个人，预算300元。"},
            extracted={"location": "北京"},
        )
        assert result.allowed is True

    def test_unknown_or_empty_not_valid(self):
        """unknown/null/空字符串不得当作有效参数继续调用。"""
        result = check_tool_requirements(
            "web_search",
            {"query": "帮我找一家明天晚上吃饭的餐厅"},
            extracted={"location": "unknown"},
        )
        assert result.allowed is False
        assert "location" in result.missing

    @pytest.mark.asyncio
    async def test_web_search_not_executed_when_missing_location(self):
        """缺 location 时 web_search 不执行，LLM 收到阻断说明并询问地点。"""
        tool = _RecordingTool("web_search")
        model = _CapturingModel("请问您想在哪个城市或区域用餐？")

        # 首次调用返回带 tool_calls 的响应（模拟 LLM 请求调用 web_search）
        response = AIMessage(
            content="需要搜索",
            tool_calls=[
                {
                    "name": "web_search",
                    "args": {"query": RESTAURANT_QUERY},
                    "id": "call_1",
                }
            ],
        )
        result = await run_tool_calls_loop(
            model,
            [],
            response,
            [tool],
            task_id="case7",
            conversation=RESTAURANT_QUERY,
        )
        assert tool.calls == 0  # web_search 未执行
        assert "请问您想在哪个城市或区域用餐？" in result
        # LLM 收到阻断说明（含缺失参数提示）
        assert any("缺少必要参数" in text for text in model.seen_inputs)
        assert any("城市/区域" in text for text in model.seen_inputs)


class TestCase8WebSearchNoHITL:
    """Case 8：web_search 默认不触发 HITL（L0 只读）。"""

    QUERY = "帮我查一下最近北京有哪些热门旅游景点。"

    def test_web_search_is_l0(self):
        from app.tools.risk import requires_approval, tool_risk_level

        assert tool_risk_level("web_search").value == "L0"
        assert requires_approval("web_search") is False

    @pytest.mark.asyncio
    async def test_human_gate_auto_approves_web_search(self):
        """默认风险分级下 web_search 直接放行，不暂停。"""
        gate = HumanApprovalGate()
        outcome = await gate.request(
            "web_search", {"query": self.QUERY}, task_id="t1"
        )
        assert outcome.decision == "approved"

    def test_approval_level_configurable(self, monkeypatch):
        """风险分级可配置：TOOL_APPROVAL_LEVEL=L0 时 web_search 也需审批。"""
        from app.config.settings import get_settings
        from app.tools.risk import requires_approval

        monkeypatch.setenv("TOOL_APPROVAL_LEVEL", "L0")
        get_settings.cache_clear()
        assert requires_approval("web_search") is True

    def test_override_tools_force_approval(self, monkeypatch):
        """TOOL_APPROVAL_OVERRIDE_TOOLS 强制审批（优先级最高，不硬编码）。"""
        from app.config.settings import get_settings

        monkeypatch.setenv("TOOL_APPROVAL_OVERRIDE_TOOLS", '["web_search"]')
        get_settings.cache_clear()
        gate = HumanApprovalGate()
        assert gate._needs_approval("web_search") is True


class TestCase9ToolFailure:
    """Case 9：工具失败不崩溃、有限重试、明确失败状态。"""

    @pytest.mark.asyncio
    async def test_transient_failure_retries_then_success(self, monkeypatch):
        """瞬时失败自动重试，成功后返回结果（有限重试）。"""
        import asyncio

        from app.agent.executor_node import _invoke_with_retry

        calls: list[int] = []

        async def flaky():
            calls.append(1)
            if len(calls) < 3:
                raise RuntimeError("connection timeout")
            return "ok"

        async def no_sleep(_):
            return None

        monkeypatch.setattr(asyncio, "sleep", no_sleep)
        result = await _invoke_with_retry(flaky, max_retries=2)
        assert result == "ok"
        assert len(calls) == 3  # 初始 1 次 + 2 次重试

    @pytest.mark.asyncio
    async def test_retry_exhausted_raises(self, monkeypatch):
        """重试耗尽后抛出异常（有上限，不无限重试）。"""
        import asyncio

        from app.agent.executor_node import _invoke_with_retry

        calls: list[int] = []

        async def always_fail():
            calls.append(1)
            raise RuntimeError("connection timeout")

        async def no_sleep(_):
            return None

        monkeypatch.setattr(asyncio, "sleep", no_sleep)
        with pytest.raises(RuntimeError):
            await _invoke_with_retry(always_fail, max_retries=2)
        assert len(calls) == 3  # 初始 1 次 + 2 次重试（上限）

    @pytest.mark.asyncio
    async def test_tool_failure_no_crash_clear_fallback(self):
        """工具抛异常：不崩溃、记录失败状态、最终回答不编造结果。"""
        tool = _FailingTool("web_search")
        model = _CapturingModel("无法完成搜索，请稍后重试。")
        response = AIMessage(
            content="需要搜索",
            tool_calls=[
                {
                    "name": "web_search",
                    "args": {"query": "北京景点"},
                    "id": "call_1",
                }
            ],
        )
        collected: list[dict] = []
        result = await run_tool_calls_loop(
            model,
            [],
            response,
            [tool],
            task_id="case9",
            extracted_requirements={"location": "北京"},
            tool_results=collected,
        )
        assert "无法完成" in result  # 明确说明无法完成，而非编造结果
        assert collected, "工具结果应被收集供 Reviewer 识别失败"
        assert collected[0]["success"] is False
        assert collected[0]["error"]

    @pytest.mark.asyncio
    async def test_sub_agent_timeout_marks_failed(self, monkeypatch, prompts):
        """子 Agent LLM 调用超时：标记失败并给出明确错误，不无限等待。"""
        from app.config.settings import get_settings

        monkeypatch.setenv("SUB_AGENT_TIMEOUT_SECONDS", "1")
        get_settings.cache_clear()

        class _HangingModel:
            def bind_tools(self, tools, **kwargs):
                return self

            async def ainvoke(self, model_input, config=None, **kwargs):
                import asyncio

                await asyncio.sleep(30)  # 远超超时阈值
                return AIMessage(content="迟到回复")

        node = SubAgentsNode(_make_provider(_HangingModel()), PromptManager)
        state = _make_state(
            execution_mode=MODE_MULTI_AGENT,
            original_user_query=TRAVEL_QUERY,
            extracted_requirements=extract_requirements(TRAVEL_QUERY),
            agent_assignments=[
                {"role": "research", "objective": "收集台北旅行信息"},
            ],
        )
        result = await node.run(state)
        assert result["agent_results"][0]["status"] == "failed"
        assert "超时" in result["agent_results"][0]["error"]


class TestCase10ReviewerContext:
    """Case 10：Reviewer 基于完整上下文生成最终结果。"""

    @pytest.mark.asyncio
    async def test_reviewer_sees_original_and_all_outputs(self, prompts):
        """Reviewer 同时看到原始需求 + 已提取参数 + A/B/C + 工具结果。"""
        model = _CapturingModel("最终报告")
        node = ReviewerNode(_make_provider(model), PromptManager)
        state = _make_state(
            execution_mode=MODE_MULTI_AGENT,
            original_user_query=TRAVEL_QUERY,
            extracted_requirements=extract_requirements(TRAVEL_QUERY),
            agent_results=[
                {
                    "role": "research",
                    "agent_name": "Research Agent",
                    "result": "结果A：台北故宫开放时间",
                    "status": "completed",
                },
                {
                    "role": "data",
                    "agent_name": "Data Agent",
                    "result": "结果B：交通预算明细",
                    "status": "completed",
                },
                {
                    "role": "writing",
                    "agent_name": "Writing Agent",
                    "result": "结果C：行程初稿",
                    "status": "completed",
                },
            ],
            tool_results=[
                {
                    "tool": "web_search",
                    "args": {"query": "台北景点"},
                    "result": "搜索结果：故宫、101",
                    "success": True,
                }
            ],
        )
        result = await node.run(state)
        text = model.seen_inputs[0]
        # 原始需求
        assert "去台北" in text
        # 已提取参数
        assert "destination" in text
        assert "budget" in text
        # 各 Agent 产出 A/B/C
        assert "结果A" in text
        assert "结果B" in text
        assert "结果C" in text
        # 工具结果
        assert "web_search" in text
        assert "故宫、101" in text
        assert result["final_result"] == "最终报告"
