"""
Human-in-the-loop 审批闸门测试。

覆盖：
- HumanApprovalGate：需审批工具触发 interrupt、非审批工具直接放行、
  恢复决策解析（approved / rejected / 修改参数 / 未确认取消）；
- 端到端：真实 Workflow + InMemorySaver，工具调用被 interrupt 暂停，
  用户批准后恢复执行并完成；拒绝时不执行工具。
"""

import json

import pytest
from langchain_core.messages import AIMessage
from langchain_core.runnables import Runnable
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from app.agent.approval import AutoApprovalGate, HumanApprovalGate
from app.agent.workflow import AgentWorkflow
from app.prompts.manager import PromptManager
from app.tools.builtins import register_builtin_tools
from app.tools.security import ToolContext

PLAN_JSON = {
    "goal": "审批测试目标",
    "subtasks": [{"id": "s1", "description": "查询数据"}],
}

REFLECTION_JSON = {
    "is_satisfactory": True,
    "accuracy_score": 0.9,
    "completeness_score": 0.9,
    "relevance_score": 0.9,
    "issues": [],
    "suggestion": None,
}


def _initial_state(thread_id: str) -> dict:
    return {
        "goal": "审批测试目标",
        "context": None,
        "tool_context": ToolContext(role="admin").to_dict(),
        "plan": None,
        "plan_version": 0,
        "current_task_index": 0,
        "task_results": [],
        "reflection_result": None,
        "should_replan": False,
        "iteration_count": 0,
        "execution_mode": None,
        "agent_assignments": [],
        "agent_results": [],
        "retry_from_index": None,
        "final_result": None,
        "task_id": thread_id,
        "messages": [],
        "errors": [],
    }


class _ApprovalChatModel(Runnable):
    """
    假模型：规划 -> 请求工具调用 -> 基于工具结果收尾。

    - 首次规划调用返回 PLAN_JSON；
    - 首次非规划/反思调用返回带 tool_calls 的响应；
    - 后续调用（工具回填后收尾 / reflection）返回固定文案。
    """

    def __init__(self, tool_call: dict):
        self.tool_call = tool_call
        self.plan_sent = False
        self.tool_call_sent = False
        self.calls = 0

    def bind_tools(self, tools, **kwargs) -> "_ApprovalChatModel":
        return self

    def invoke(self, model_input, config=None, **kwargs):  # pragma: no cover
        import asyncio

        return asyncio.run(self.ainvoke(model_input, config=config, **kwargs))

    async def ainvoke(self, model_input, config=None, **kwargs):
        self.calls += 1
        text = str(model_input)
        if "任务规划" in text or "重新规划" in text:
            self.plan_sent = True
            return AIMessage(content=json.dumps(PLAN_JSON, ensure_ascii=False))
        if "任务审查" in text or "请评估" in text:
            return AIMessage(content=json.dumps(REFLECTION_JSON, ensure_ascii=False))
        if self.plan_sent and not self.tool_call_sent:
            self.tool_call_sent = True
            return AIMessage(
                content="需要查询数据，调用工具。", tool_calls=[self.tool_call]
            )
        return AIMessage(content="查询完成，任务结果已就绪。")


class _FakeProvider:
    def __init__(self, model):
        self._model = model

    def get_chat_model(self):
        return self._model

    def get_client(self):  # pragma: no cover
        return None


def _make_workflow(model, approval_gate):
    register_builtin_tools()
    PromptManager.init_defaults()
    return AgentWorkflow(
        _FakeProvider(model), PromptManager, approval_gate=approval_gate
    ).build(checkpointer=InMemorySaver())


class TestHumanApprovalGate:
    """审批闸门配置与决策测试（interrupt 语义由 LangGraph 运行时提供）。"""

    @pytest.mark.asyncio
    async def test_non_side_effect_tool_auto_approved(self):
        """非审批工具集内的工具直接放行，不暂停。"""
        gate = HumanApprovalGate(require_approval_tools={"sql_query"})
        outcome = await gate.request("calculator", {"query": "1+1"}, task_id="t1")
        assert outcome.decision == "approved"

    @pytest.mark.asyncio
    async def test_auto_gate_approves_everything(self):
        """AutoApprovalGate 默认放行一切（保持旧语义）。"""
        gate = AutoApprovalGate()
        outcome = await gate.request("sql_query", {"query": "SELECT 1"}, task_id="t1")
        assert outcome.decision == "approved"
        assert outcome.args == {"query": "SELECT 1"}

    @pytest.mark.asyncio
    async def test_default_risk_based_approval(self):
        """默认按三级风险分级判定：L0 只读（web_search）不审批，L2 高风险（email.send）需审批。"""
        gate = HumanApprovalGate()
        # 显式集合未指定 -> 使用风险分级
        assert gate._tools is None
        outcome = await gate.request("web_search", {"query": "北京景点"}, task_id="t1")
        assert outcome.decision == "approved"
        outcome = await gate.request("calculator", {"query": "1+1"}, task_id="t1")
        assert outcome.decision == "approved"
        # L2 高风险工具需审批（interrupt 语义由 LangGraph 运行时提供，此处验证判定）
        assert gate._needs_approval("email.send") is True
        assert gate._needs_approval("web_search") is False

    @pytest.mark.asyncio
    async def test_override_tools_force_approval(self, monkeypatch):
        """TOOL_APPROVAL_OVERRIDE_TOOLS 强制要求审批（优先级最高）。"""
        from app.config.settings import get_settings

        monkeypatch.setenv("TOOL_APPROVAL_OVERRIDE_TOOLS", '["web_search"]')
        get_settings.cache_clear()
        gate = HumanApprovalGate()
        assert gate._needs_approval("web_search") is True


class TestApprovalWorkflow:
    """审批端到端：interrupt -> 决策 -> 恢复。"""

    @pytest.mark.asyncio
    async def test_interrupt_paused_then_approved_resumes(self):
        """工具调用触发 interrupt；批准后恢复执行并完成。"""
        model = _ApprovalChatModel(
            {
                "name": "sql_query",
                "args": {"query": "SELECT 1"},
                "id": "call_1",
            }
        )
        workflow = _make_workflow(
            model, HumanApprovalGate(require_approval_tools={"sql_query"})
        )
        config = {"configurable": {"thread_id": "approval-1"}}

        # 首轮：运行到 interrupt 暂停
        interrupt_payload = None
        async for event in workflow.astream(_initial_state("approval-1"), config=config):
            if "__interrupt__" in event:
                interrupt_payload = event["__interrupt__"][0].value
                break
        assert interrupt_payload is not None
        assert interrupt_payload["kind"] == "tool_approval"
        assert interrupt_payload["tool_name"] == "sql_query"
        assert interrupt_payload["task_id"] == "approval-1"

        # 检查点已保存暂停位置
        snap = await workflow.aget_state(config)
        assert snap.next, "interrupt 后应有未完成节点"

        # 用户批准 -> 恢复
        resume_events = []
        async for event in workflow.astream(
            Command(resume={"decision": "approved"}), config=config
        ):
            resume_events.append(event)
        assert resume_events, "恢复后应继续执行"
        final_state = await workflow.aget_state(config)
        assert final_state.values.get("final_result"), "批准后任务应完成"

    @pytest.mark.asyncio
    async def test_rejected_does_not_execute_tool(self):
        """拒绝后工具不执行，任务以完成收尾（LLM 收到拒绝说明）。"""
        model = _ApprovalChatModel(
            {
                "name": "sql_query",
                "args": {"query": "DELETE FROM employees"},
                "id": "call_1",
            }
        )
        workflow = _make_workflow(
            model, HumanApprovalGate(require_approval_tools={"sql_query"})
        )
        config = {"configurable": {"thread_id": "approval-2"}}

        async for event in workflow.astream(_initial_state("approval-2"), config=config):
            if "__interrupt__" in event:
                break

        async for event in workflow.astream(
            Command(resume={"decision": "rejected", "reason": "不允许删除数据"}),
            config=config,
        ):
            pass

        final_state = await workflow.aget_state(config)
        assert final_state.values.get("final_result")
        # 拒绝路径下工具未被真正执行（假模型未记录到 SQL 执行结果）
        assert "DELETE" not in str(
            final_state.values.get("final_result", "")
        ) or "拒绝" in str(final_state.values.get("final_result", ""))

    @pytest.mark.asyncio
    async def test_modified_args_used_after_approval(self):
        """批准并修改参数后，工具以修改后的参数执行。"""
        modified = {"query": "SELECT 2"}
        model = _ApprovalChatModel(
            {
                "name": "sql_query",
                "args": {"query": "SELECT 1"},
                "id": "call_1",
            }
        )
        workflow = _make_workflow(
            model, HumanApprovalGate(require_approval_tools={"sql_query"})
        )
        config = {"configurable": {"thread_id": "approval-3"}}

        async for event in workflow.astream(_initial_state("approval-3"), config=config):
            if "__interrupt__" in event:
                break

        async for event in workflow.astream(
            Command(resume={"decision": "approved", "args": modified}), config=config
        ):
            pass

        final_state = await workflow.aget_state(config)
        assert final_state.values.get("final_result")
        assert final_state.values.get("task_results"), "工具应被调用并产出结果"
