"""
Agent Workflow 和节点单元测试。
测试状态定义、节点逻辑、Workflow 路由等。
"""

import pytest

from app.agent.executor_node import ExecutorNode, ToolExecutionPolicy
from app.agent.state import AgentState
from app.models.plan import Plan, ReflectionResult
from app.models.task import SubTask, TaskStatus
from app.tools.builtins import register_builtin_tools
from app.tools.registry import ToolRegistry
from app.tools.security import ROLE_GUEST, ROLE_USER, ToolContext


class TestAgentState:
    """AgentState 状态定义测试。"""

    def test_state_creation(self):
        """测试状态创建。"""
        state: AgentState = {
            "goal": "测试目标",
            "context": "测试上下文",
            "plan": None,
            "plan_version": 0,
            "current_task_index": 0,
            "task_results": [],
            "reflection_result": None,
            "should_replan": False,
            "iteration_count": 0,
            "final_result": None,
            "task_id": "test-123",
            "messages": [],
            "errors": [],
        }
        assert state["goal"] == "测试目标"
        assert state["plan_version"] == 0
        assert state["should_replan"] is False

    def test_state_with_plan(self):
        """测试带计划的状态。"""
        plan = {"goal": "test", "subtasks": [{"id": "t1", "description": "task 1"}]}
        state: AgentState = {
            "goal": "test",
            "context": None,
            "plan": plan,
            "plan_version": 1,
            "current_task_index": 0,
            "task_results": [],
            "reflection_result": None,
            "should_replan": False,
            "iteration_count": 0,
            "final_result": None,
            "task_id": "test-456",
            "messages": [],
            "errors": [],
        }
        assert state["plan"]["subtasks"][0]["id"] == "t1"


class TestDataModels:
    """数据模型测试。"""

    def test_subtask_creation(self):
        """测试子任务模型创建。"""
        subtask = SubTask(
            id="task_1",
            description="搜索相关资料",
            dependencies=[],
        )
        assert subtask.id == "task_1"
        assert subtask.status == TaskStatus.PENDING
        assert subtask.result is None

    def test_plan_creation(self):
        """测试计划模型创建。"""
        plan = Plan(
            goal="测试目标",
            subtasks=[
                SubTask(id="task_1", description="子任务1"),
                SubTask(id="task_2", description="子任务2", dependencies=["task_1"]),
            ],
            version=1,
        )
        assert len(plan.subtasks) == 2
        assert plan.subtasks[1].dependencies == ["task_1"]

    def test_reflection_result(self):
        """测试反思结果模型。"""
        result = ReflectionResult(
            is_satisfactory=True,
            accuracy_score=0.9,
            completeness_score=0.8,
            relevance_score=0.85,
            issues=[],
            suggestion=None,
        )
        assert result.is_satisfactory is True
        assert result.accuracy_score >= 0.0 and result.accuracy_score <= 1.0

    def test_task_status_enum(self):
        """测试任务状态枚举值。"""
        assert TaskStatus.PENDING.value == "pending"
        assert TaskStatus.PLANNING.value == "planning"
        assert TaskStatus.EXECUTING.value == "executing"
        assert TaskStatus.REFLECTING.value == "reflecting"
        assert TaskStatus.COMPLETED.value == "completed"
        assert TaskStatus.FAILED.value == "failed"
        assert TaskStatus.REPLANNING.value == "replanning"


class TestToolRegistry:
    """工具注册表测试。"""

    def test_empty_registry(self):
        """测试空注册表。"""
        assert ToolRegistry.get_all() == {}
        assert ToolRegistry.get_tool_descriptions() == "当前无可用工具。"
        assert ToolRegistry.get_all_langchain_tools() == []

    def test_get_nonexistent_tool(self):
        """测试获取不存在的工具。"""
        assert ToolRegistry.get("nonexistent") is None

    def test_register_and_get(self):
        """测试注册和获取工具。"""
        register_builtin_tools()
        tool = ToolRegistry.get("datetime_tool")
        assert tool is not None
        assert tool.name == "datetime_tool"


class _FakeTool:
    """最小化的假工具，记录是否被真正调用。"""

    def __init__(self, name: str):
        self.name = name
        self.invoked = False
        self.invoked_args = None

    async def ainvoke(self, args):
        self.invoked = True
        self.invoked_args = args
        return f"{self.name} executed"


class _FakeResponse:
    """模拟带 tool_calls 的 LLM 响应。"""

    def __init__(self, tool_calls):
        self.tool_calls = tool_calls
        self.content = ""


class _FakeLLMWithTools:
    """模拟 bind_tools 后的 LLM，记录最终回填的消息。"""

    def __init__(self):
        self.last_messages = None

    async def ainvoke(self, messages):
        self.last_messages = messages
        return _FakeResponse(tool_calls=[])  # content 为空即可，用于收敛


def _make_executor(approval_hook=None) -> ExecutorNode:
    """构造一个绕过真实 LLM 依赖的 ExecutorNode，仅用于测试执行边界。"""
    from unittest.mock import MagicMock

    llm_provider = MagicMock()
    llm_provider.get_chat_model.return_value = MagicMock()
    prompt_manager = MagicMock()
    return ExecutorNode(llm_provider, prompt_manager, tool_approval_hook=approval_hook)


class TestToolExecutionPolicy:
    """智能体层工具执行边界（白名单 + 副作用审批）测试。"""

    def test_unregistered_tool_denied_by_default(self):
        """未登记工具名默认拒绝。"""
        policy = ToolExecutionPolicy(allowed_tools={"calculator"})
        allowed, reason = policy.check("unknown_tool", {})
        assert allowed is False
        assert reason and "unknown_tool" in reason

    def test_registered_safe_tool_allowed(self):
        """已登记的无副作用工具放行。"""
        policy = ToolExecutionPolicy(allowed_tools={"calculator"})
        allowed, reason = policy.check("calculator", {})
        assert allowed is True
        assert reason is None

    def test_side_effect_tool_denied_when_hook_rejects(self):
        """副作用工具在审批钩子拒绝时被阻止。"""
        policy = ToolExecutionPolicy(
            allowed_tools={"sql_query"},
            approval_hook=lambda name, args: False,
        )
        allowed, reason = policy.check("sql_query", {"query": "SELECT 1"})
        assert allowed is False
        assert reason and "sql_query" in reason

    def test_side_effect_tool_allowed_when_hook_approves(self):
        """副作用工具在审批钩子放行时允许执行。"""
        policy = ToolExecutionPolicy(
            allowed_tools={"file_processing"},
            approval_hook=lambda name, args: True,
        )
        allowed, reason = policy.check("file_processing", {"query": "a.txt"})
        assert allowed is True

    def test_hook_exception_treated_as_deny(self):
        """审批钩子异常时按拒绝处理，避免默认放行。"""
        def boom(name, args):
            raise RuntimeError("hook down")

        policy = ToolExecutionPolicy(
            allowed_tools={"web_search"}, approval_hook=boom
        )
        allowed, reason = policy.check("web_search", {})
        assert allowed is False
        assert reason and "web_search" in reason

    def test_guest_role_denied_by_permission_matrix(self):
        """已注册但角色无权的工具（guest -> sql）仍被拒绝："已注册" != "允许"。"""
        policy = ToolExecutionPolicy(
            allowed_tools={"sql_query"}, context=ToolContext(role=ROLE_GUEST)
        )
        allowed, reason = policy.check("sql_query", {"query": "SELECT 1"})
        assert allowed is False
        assert "无权" in reason

    def test_user_role_allowed_by_permission_matrix(self):
        """user 角色可以调用已注册的 SQL 工具。"""
        policy = ToolExecutionPolicy(
            allowed_tools={"sql_query"}, context=ToolContext(role=ROLE_USER)
        )
        allowed, reason = policy.check("sql_query", {"query": "SELECT 1"})
        assert allowed is True
        assert reason is None


class TestExecutorToolBoundary:
    """Executor 调用链上的执行边界集成测试。"""

    @pytest.mark.asyncio
    async def test_unwhitelisted_tool_blocked_and_logged(self, monkeypatch):
        """LLM 请求调用不在白名单中的工具时，执行被阻止并记录原因。"""
        import app.agent.executor_node as exec_mod

        # 捕获执行边界拒绝时的告警日志
        warnings: list = []
        monkeypatch.setattr(
            exec_mod.logger,
            "warning",
            lambda *args, **kwargs: warnings.append((args, kwargs)),
        )

        safe_tool = _FakeTool("calculator")
        llm_with_tools = _FakeLLMWithTools()
        executor = _make_executor()

        # LLM 请求调用一个未登记（不在白名单）的工具
        response = _FakeResponse(
            tool_calls=[
                {"name": "malicious_tool", "args": {"cmd": "rm -rf /"}, "id": "call_1"}
            ]
        )

        await executor._execute_tool_calls(
            response, tools=[safe_tool], messages=[], llm_with_tools=llm_with_tools
        )

        # 未登记工具不会被执行
        assert safe_tool.invoked is False
        # 记录了拒绝原因
        assert warnings, "应记录被拒绝的工具调用"
        _, kwargs = warnings[0]
        assert kwargs.get("tool") == "malicious_tool"
        assert "malicious_tool" in (kwargs.get("reason") or "")
        # 回填给 LLM 的消息包含拒绝说明
        blocked = [
            m
            for m in llm_with_tools.last_messages
            if getattr(m, "content", "").startswith("工具调用被拒绝")
        ]
        assert blocked, "应向 LLM 回填一条拒绝说明消息"

    @pytest.mark.asyncio
    async def test_side_effect_tool_blocked_by_hook(self, monkeypatch):
        """副作用工具在审批钩子拒绝时不会被执行。"""
        import app.agent.executor_node as exec_mod

        warnings: list = []
        monkeypatch.setattr(
            exec_mod.logger,
            "warning",
            lambda *args, **kwargs: warnings.append((args, kwargs)),
        )

        sql_tool = _FakeTool("sql_query")
        llm_with_tools = _FakeLLMWithTools()
        # 注入一个拒绝所有副作用工具的审批钩子
        executor = _make_executor(approval_hook=lambda name, args: False)

        response = _FakeResponse(
            tool_calls=[
                {"name": "sql_query", "args": {"query": "SELECT 1"}, "id": "call_2"}
            ]
        )

        await executor._execute_tool_calls(
            response, tools=[sql_tool], messages=[], llm_with_tools=llm_with_tools
        )

        assert sql_tool.invoked is False
        assert warnings, "审批被拒绝应记录原因"

    @pytest.mark.asyncio
    async def test_whitelisted_safe_tool_executes(self):
        """已登记的无副作用工具正常执行。"""
        calc_tool = _FakeTool("calculator")
        llm_with_tools = _FakeLLMWithTools()
        executor = _make_executor()

        response = _FakeResponse(
            tool_calls=[{"name": "calculator", "args": {"query": "1+1"}, "id": "c3"}]
        )

        await executor._execute_tool_calls(
            response, tools=[calc_tool], messages=[], llm_with_tools=llm_with_tools
        )

        assert calc_tool.invoked is True

    @pytest.mark.asyncio
    async def test_guest_role_tool_call_blocked(self, monkeypatch):
        """guest 角色请求调用 sql 类工具时，权限矩阵拒绝且工具不被执行。"""

        sql_tool = _FakeTool("sql_query")
        llm_with_tools = _FakeLLMWithTools()
        executor = _make_executor()
        guest_ctx = ToolContext(role=ROLE_GUEST)

        response = _FakeResponse(
            tool_calls=[{"name": "sql_query", "args": {"query": "SELECT 1"}, "id": "call_g"}]
        )

        await executor._execute_tool_calls(
            response,
            tools=[sql_tool],
            messages=[],
            llm_with_tools=llm_with_tools,
            tool_context=guest_ctx,
        )

        # 越权调用不会触达工具实现
        assert sql_tool.invoked is False
        # 回填给 LLM 的消息包含权限拒绝说明
        blocked = [
            m
            for m in llm_with_tools.last_messages
            if getattr(m, "content", "").startswith("工具调用被拒绝")
        ]
        assert blocked, "应向 LLM 回填权限拒绝说明"


class TestPromptLayering:
    """Prompt 输入分层与外部数据标记测试（Prompt Injection 防护）。"""

    def test_executor_prompt_layered(self):
        """Executor Prompt：system 纯规则，子任务与历史结果位于 human 层。"""
        from langchain_core.prompts import SystemMessagePromptTemplate

        from app.prompts.executor import EXECUTOR_PROMPT, EXECUTOR_SYSTEM_PROMPT

        messages = EXECUTOR_PROMPT.messages
        assert isinstance(messages[0], SystemMessagePromptTemplate)
        # human 层包含子任务描述与之前结果（保持“当前子任务”段落结构）
        human_prompt = messages[1]
        assert "subtask_description" in getattr(human_prompt, "input_variables", [])
        assert "previous_results" in getattr(human_prompt, "input_variables", [])
        # 外部数据安全规则已注入 system
        assert "external_knowledge" in EXECUTOR_SYSTEM_PROMPT
        assert "不执行" in EXECUTOR_SYSTEM_PROMPT

    def test_planner_prompt_external_data_rule(self):
        """Planner Prompt：包含外部数据标记说明与注入防护规则。"""
        from app.prompts.planner import PLANNER_SYSTEM_PROMPT

        assert "external_knowledge" in PLANNER_SYSTEM_PROMPT
        assert "忽略" in PLANNER_SYSTEM_PROMPT
