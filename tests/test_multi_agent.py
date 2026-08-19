"""
多 Agent 协作（Supervisor 模式）测试。

覆盖：
- SupervisorNode：multi_agent 分配 / single 回退 / 角色清洗（未知角色回退 general）；
- SubAgentsNode：按分配顺序执行、角色工具范围控制、失败不阻断后续；
- ReviewerNode：合成最终结果；
- 工作流路由：_route_after_supervisor 的 multi_agent / single 决策。
"""

import json
from unittest.mock import MagicMock

import pytest
from langchain_core.runnables import Runnable

from app.agent.multi_agent import (
    AGENT_ROLES,
    GENERIC_ROLE,
    MODE_MULTI_AGENT,
    MODE_SINGLE,
    ReviewerNode,
    SubAgentsNode,
    SupervisorNode,
)
from app.agent.workflow import AgentWorkflow
from app.prompts.manager import PromptManager
from app.tools.registry import ToolRegistry
from app.tools.security import CATEGORY_NETWORK, CATEGORY_SQL

SUPERVISOR_MULTI_JSON = json.dumps(
    {
        "mode": "multi_agent",
        "agents": [
            {"role": "research", "objective": "搜索行业资料并整理关键数据"},
            {"role": "writing", "objective": "基于研究结果撰写报告"},
        ],
        "reasoning": "需要搜索资料并撰写报告，跨领域协作",
    },
    ensure_ascii=False,
)

SUPERVISOR_SINGLE_JSON = json.dumps(
    {
        "mode": "single",
        "agents": [],
        "reasoning": "简单任务",
    },
    ensure_ascii=False,
)


class _FakeModel(Runnable):
    """按调用顺序返回固定响应的假模型（含 bind_tools 兼容）。"""

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
        from langchain_core.messages import AIMessage

        return AIMessage(content=response)


def _make_provider(model) -> MagicMock:
    provider = MagicMock()
    provider.get_chat_model = MagicMock(return_value=model)
    provider.get_client = MagicMock(return_value=MagicMock())
    return provider


def _make_state(**overrides) -> dict:
    state = {
        "goal": "调研 AI 行业并撰写报告",
        "context": None,
        "tool_context": None,
        "task_id": "task-multi-1",
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


class TestSupervisorNode:
    """Supervisor 编排决策测试。"""

    @pytest.mark.asyncio
    async def test_multi_agent_assignments(self, prompts):
        node = SupervisorNode(
            _make_provider(_FakeModel([SUPERVISOR_MULTI_JSON])), PromptManager
        )
        result = await node.run(_make_state())
        assert result["execution_mode"] == MODE_MULTI_AGENT
        assert [a["role"] for a in result["agent_assignments"]] == [
            "research",
            "writing",
        ]
        assert result["agent_assignments"][0]["objective"]

    @pytest.mark.asyncio
    async def test_single_mode_fallback(self, prompts):
        node = SupervisorNode(
            _make_provider(_FakeModel([SUPERVISOR_SINGLE_JSON])), PromptManager
        )
        result = await node.run(_make_state())
        assert result["execution_mode"] == MODE_SINGLE
        assert not result.get("agent_assignments")

    @pytest.mark.asyncio
    async def test_parse_failure_falls_back_to_single(self, prompts):
        """LLM 返回非 JSON 时回退单 Agent 流程，不阻断任务。"""
        node = SupervisorNode(
            _make_provider(_FakeModel(["直接完成子任务，无需调用工具。"])), PromptManager
        )
        result = await node.run(_make_state())
        assert result["execution_mode"] == MODE_SINGLE

    @pytest.mark.asyncio
    async def test_unknown_role_mapped_to_general(self, prompts):
        """未知角色清洗为 general，而非直接拒绝分配。"""
        payload = json.dumps(
            {
                "mode": "multi_agent",
                "agents": [{"role": "astro_agent", "objective": "分析星象"}],
                "reasoning": "测试未知角色",
            },
            ensure_ascii=False,
        )
        node = SupervisorNode(_make_provider(_FakeModel([payload])), PromptManager)
        result = await node.run(_make_state())
        assert result["execution_mode"] == MODE_MULTI_AGENT
        assert result["agent_assignments"][0]["role"] == "general"

    @pytest.mark.asyncio
    async def test_empty_objectives_dropped(self, prompts):
        """空 objective 的分配被丢弃；全部为空则回退 single。"""
        payload = json.dumps(
            {
                "mode": "multi_agent",
                "agents": [
                    {"role": "research", "objective": ""},
                    {"role": "writing", "objective": "   "},
                ],
                "reasoning": "无有效目标",
            },
            ensure_ascii=False,
        )
        node = SupervisorNode(_make_provider(_FakeModel([payload])), PromptManager)
        result = await node.run(_make_state())
        assert result["execution_mode"] == MODE_SINGLE


class TestAgentRoles:
    """角色注册表与工具范围控制。"""

    def test_builtin_roles_exist(self):
        assert set(AGENT_ROLES) == {"research", "data", "coding", "writing", "review"}

    def test_research_role_tool_categories(self):
        """research 仅绑定网络类别（无 SQL/文件）。"""
        assert AGENT_ROLES["research"].tool_categories == frozenset(
            {CATEGORY_NETWORK}
        )

    def test_generic_role_allows_all(self):
        assert GENERIC_ROLE.tool_categories == frozenset(
            {"system", "sql", "file", "network"}
        )


class TestSubAgentsNode:
    """子 Agent 顺序执行与产出汇总。"""

    @pytest.mark.asyncio
    async def test_executes_assignments_in_order(self, prompts):
        """按 Supervisor 分配顺序执行，产出按序收集。"""
        model = _FakeModel(
            [
                "研究结论：AI 市场规模 2026 年达 5000 亿",
                "报告已撰写完成（基于研究结论）",
            ]
        )
        node = SubAgentsNode(_make_provider(model), PromptManager)
        state = _make_state(
            execution_mode=MODE_MULTI_AGENT,
            agent_assignments=[
                {"role": "research", "objective": "搜索行业资料"},
                {"role": "writing", "objective": "撰写报告"},
            ],
        )
        result = await node.run(state)
        assert [r["role"] for r in result["agent_results"]] == ["research", "writing"]
        assert result["agent_results"][0]["status"] == "completed"
        assert "5000 亿" in result["agent_results"][0]["result"]
        assert model.calls == 2

    @pytest.mark.asyncio
    async def test_no_assignments_returns_error(self, prompts):
        node = SubAgentsNode(_make_provider(_FakeModel([])), PromptManager)
        result = await node.run(_make_state())
        assert result["agent_results"] == []
        assert any("无 Agent 分配" in e for e in result["errors"])

    @pytest.mark.asyncio
    async def test_role_tool_scope_controls_registry_lookup(self, prompts):
        """子 Agent 按角色类别获取工具（此处工具未注册 -> 空集 -> 直接 LLM）。"""
        import app.agent.multi_agent as multi_mod

        captured: list = []
        original = ToolRegistry.get_langchain_tools_by_categories

        def spy(categories):
            captured.append(frozenset(categories))
            return []

        multi_mod.ToolRegistry.get_langchain_tools_by_categories = staticmethod(spy)
        try:
            node = SubAgentsNode(
                _make_provider(_FakeModel(["数据整理完成"])), PromptManager
            )
            state = _make_state(
                execution_mode=MODE_MULTI_AGENT,
                agent_assignments=[
                    {"role": "data", "objective": "整理销售数据"},
                ],
            )
            result = await node.run(state)
            assert result["agent_results"][0]["status"] == "completed"
        finally:
            multi_mod.ToolRegistry.get_langchain_tools_by_categories = original
        # data 角色仅 SQL + system 类别
        assert captured[0] == frozenset({CATEGORY_SQL, "system"})

    @pytest.mark.asyncio
    async def test_failed_agent_does_not_block_others(self, prompts):
        """某个子 Agent 失败不阻断后续 Agent 执行。"""
        from langchain_core.messages import AIMessage

        class _FlakyModel:
            def __init__(self):
                self.calls = 0

            def bind_tools(self, tools, **kwargs):
                return self

            async def ainvoke(self, model_input, config=None, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    raise RuntimeError("LLM 内部错误")
                return AIMessage(content="第二个 Agent 正常完成")

        node = SubAgentsNode(_make_provider(_FlakyModel()), PromptManager)
        state = _make_state(
            execution_mode=MODE_MULTI_AGENT,
            agent_assignments=[
                {"role": "research", "objective": "搜索资料"},
                {"role": "writing", "objective": "撰写报告"},
            ],
        )
        result = await node.run(state)
        assert result["agent_results"][0]["status"] == "failed"
        assert result["agent_results"][0]["error"]
        assert result["agent_results"][1]["status"] == "completed"

    @pytest.mark.asyncio
    async def test_passes_previous_results_to_next_agent(self, prompts):
        """前序 Agent 产出注入后续 Agent 的 human 消息。"""
        from langchain_core.messages import AIMessage

        seen_inputs: list[str] = []

        class _CapturingModel:
            def bind_tools(self, tools, **kwargs):
                return self

            async def ainvoke(self, model_input, config=None, **kwargs):
                text = str(model_input)
                seen_inputs.append(text)
                return AIMessage(content="完成")

        node = SubAgentsNode(_make_provider(_CapturingModel()), PromptManager)
        state = _make_state(
            execution_mode=MODE_MULTI_AGENT,
            agent_assignments=[
                {"role": "research", "objective": "搜索资料"},
                {"role": "writing", "objective": "撰写报告"},
            ],
        )
        await node.run(state)
        assert "research" in seen_inputs[1]
        assert "完成" in seen_inputs[1]


class TestReviewerNode:
    """Reviewer 最终合成测试。"""

    @pytest.mark.asyncio
    async def test_reviewer_synthesizes_final_result(self, prompts):
        node = ReviewerNode(
            _make_provider(_FakeModel(["最终报告：整合完成，无矛盾发现"])),
            PromptManager,
        )
        state = _make_state(
            execution_mode=MODE_MULTI_AGENT,
            agent_results=[
                {
                    "role": "research",
                    "objective": "搜索资料",
                    "result": "市场规模 5000 亿",
                    "status": "completed",
                    "error": None,
                }
            ],
        )
        result = await node.run(state)
        assert result["final_result"]
        assert "最终报告" in result["final_result"]

    @pytest.mark.asyncio
    async def test_reviewer_failure_keeps_agent_outputs(self, prompts):
        """评审失败时仍保留各 Agent 产出作为最终结果（不丢失工作）。"""

        class _BoomModel:
            def bind_tools(self, tools, **kwargs):
                return self

            async def ainvoke(self, model_input, config=None, **kwargs):
                raise RuntimeError("评审超时")

        node = ReviewerNode(_make_provider(_BoomModel()), PromptManager)
        state = _make_state(
            execution_mode=MODE_MULTI_AGENT,
            agent_results=[
                {
                    "role": "research",
                    "objective": "搜索资料",
                    "result": "关键数据已收集",
                    "status": "completed",
                    "error": None,
                }
            ],
        )
        result = await node.run(state)
        assert "Multi" in result["final_result"] or "评审失败" in result["final_result"]
        assert "关键数据已收集" in result["final_result"]


class TestWorkflowRouting:
    """Supervisor 后的条件路由。"""

    def test_multi_agent_route(self):
        builder = AgentWorkflow(
            _make_provider(_FakeModel([])), PromptManager
        )
        assert (
            builder._route_after_supervisor(
                _make_state(
                    execution_mode=MODE_MULTI_AGENT,
                    agent_assignments=[{"role": "research", "objective": "x"}],
                )
            )
            == "multi_agent"
        )

    def test_single_route_without_assignments(self):
        builder = AgentWorkflow(_make_provider(_FakeModel([])), PromptManager)
        assert (
            builder._route_after_supervisor(_make_state(execution_mode=MODE_MULTI_AGENT))
            == "single"
        )

    def test_single_route_by_default(self):
        builder = AgentWorkflow(_make_provider(_FakeModel([])), PromptManager)
        assert builder._route_after_supervisor(_make_state()) == "single"
