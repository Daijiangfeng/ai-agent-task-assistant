"""
LangGraph Checkpoint 启用测试。

覆盖：
- create_checkpointer 工厂：关闭/内存/auto 降级/postgres 强要求失败；
- 真实 Workflow 在启用 checkpointer 后按 thread_id 写入检查点，
  同线程重放不再执行节点（断点续跑基础）。
"""

import json

import pytest
from langchain_core.messages import AIMessage
from langchain_core.runnables import Runnable
from langgraph.checkpoint.memory import InMemorySaver

from app.agent.checkpoint import create_checkpointer
from app.agent.workflow import AgentWorkflow
from app.config.settings import Settings
from app.prompts.manager import PromptManager
from app.tools.builtins import register_builtin_tools

REFLECTION_JSON = {
    "is_satisfactory": True,
    "accuracy_score": 0.9,
    "completeness_score": 0.9,
    "relevance_score": 0.9,
    "issues": [],
    "suggestion": None,
}

PLAN_JSON = {
    "goal": "检查点测试目标",
    "subtasks": [{"id": "s1", "description": "直接完成"}],
}


def _initial_state(thread_id: str) -> dict:
    return {
        "goal": "检查点测试目标",
        "context": None,
        "tool_context": None,
        "plan": None,
        "plan_version": 0,
        "current_task_index": 0,
        "task_results": [],
        "reflection_result": None,
        "should_replan": False,
        "iteration_count": 0,
        "final_result": None,
        "task_id": thread_id,
        "messages": [],
        "errors": [],
    }


class _FakeChatModel(Runnable):
    """按 Prompt 文本区分节点返回固定响应的假模型，记录调用次数。"""

    def __init__(self):
        self.calls: list[str] = []

    def bind_tools(self, tools, **kwargs) -> "_FakeChatModel":
        return self

    def invoke(self, model_input, config=None, **kwargs):  # pragma: no cover
        import asyncio

        return asyncio.run(self.ainvoke(model_input, config=config, **kwargs))

    async def ainvoke(self, model_input, config=None, **kwargs):
        text = str(model_input)
        self.calls.append(text)
        if "任务审查" in text or "请评估" in text:
            return AIMessage(content=json.dumps(REFLECTION_JSON, ensure_ascii=False))
        if "任务规划" in text or "重新规划" in text:
            return AIMessage(content=json.dumps(PLAN_JSON, ensure_ascii=False))
        return AIMessage(content="直接完成子任务，无需调用工具。")


class _FakeProvider:
    def __init__(self, model):
        self._model = model

    def get_chat_model(self):
        return self._model

    def get_client(self):  # pragma: no cover - 兼容接口
        return None


class TestCheckpointerFactory:
    """create_checkpointer 后端选择。"""

    @pytest.mark.asyncio
    async def test_disabled_returns_none(self):
        assert await create_checkpointer(Settings(ENABLE_CHECKPOINTING=False)) is None

    @pytest.mark.asyncio
    async def test_memory_backend(self):
        cp = await create_checkpointer(
            Settings(ENABLE_CHECKPOINTING=True, CHECKPOINT_BACKEND="memory")
        )
        assert isinstance(cp, InMemorySaver)

    @pytest.mark.asyncio
    async def test_auto_falls_back_to_memory_without_postgres(self):
        cp = await create_checkpointer(
            Settings(
                ENABLE_CHECKPOINTING=True,
                CHECKPOINT_BACKEND="auto",
                POSTGRES_HOST="127.0.0.1",
                POSTGRES_PORT=1,
                DB_CONNECT_TIMEOUT=1,
            )
        )
        assert isinstance(cp, InMemorySaver)

    @pytest.mark.asyncio
    async def test_postgres_backend_raises_without_postgres(self):
        with pytest.raises(RuntimeError):
            await create_checkpointer(
                Settings(
                    ENABLE_CHECKPOINTING=True,
                    CHECKPOINT_BACKEND="postgres",
                    POSTGRES_HOST="127.0.0.1",
                    POSTGRES_PORT=1,
                    DB_CONNECT_TIMEOUT=1,
                )
            )


class TestWorkflowCheckpointing:
    """真实 Workflow 检查点写入与断点续跑。"""

    @pytest.mark.asyncio
    async def test_checkpoint_written_and_resume_skips_done_nodes(self):
        register_builtin_tools()
        PromptManager.init_defaults()

        model = _FakeChatModel()
        workflow = AgentWorkflow(
            _FakeProvider(model), PromptManager
        ).build(checkpointer=InMemorySaver())
        config = {"configurable": {"thread_id": "thread-1"}}

        # 首次运行只消费 Supervisor + Planner（+ Executor 事件确认 planner 超步已落盘）
        # 后中断（模拟服务崩溃/中断）
        first_events = []
        async for event in workflow.astream(_initial_state("thread-1"), config=config):
            first_events.append(event)
            if len(first_events) >= 3:
                break

        # 检查点已写入：Planner 产物已持久化，且存在未完成任务
        snap = await workflow.aget_state(config)
        assert snap is not None
        assert snap.values.get("plan") is not None
        assert snap.values.get("task_id") == "thread-1"
        assert snap.values.get("execution_mode") in ("single", "multi_agent")
        assert snap.next, "检查点应记录未完成的后续任务"

        # 断点续跑：以 None 作为输入重提同一 thread，已完成节点不重跑
        resume_events = []
        async for event in workflow.astream(None, config=config):
            resume_events.append(event)
        assert resume_events, "续跑应继续产出后续节点事件"
        assert not any("planner" in e or "supervisor" in e for e in resume_events), (
            "Planner 与 Supervisor 不应重跑"
        )

        snap2 = await workflow.aget_state(config)
        assert snap2.values.get("final_result"), "续跑应完成并产出最终结果"

    @pytest.mark.asyncio
    async def test_threads_isolated_by_thread_id(self):
        register_builtin_tools()
        PromptManager.init_defaults()

        workflow = AgentWorkflow(
            _FakeProvider(_FakeChatModel()), PromptManager
        ).build(checkpointer=InMemorySaver())

        config_a = {"configurable": {"thread_id": "task-A"}}
        config_b = {"configurable": {"thread_id": "task-B"}}
        async for _ in workflow.astream(_initial_state("task-A"), config=config_a):
            pass
        async for _ in workflow.astream(_initial_state("task-B"), config=config_b):
            pass

        snap_a = await workflow.aget_state(config_a)
        snap_b = await workflow.aget_state(config_b)
        assert snap_a.values.get("task_id") == "task-A"
        assert snap_b.values.get("task_id") == "task-B"
