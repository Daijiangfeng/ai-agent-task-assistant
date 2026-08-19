"""
Agent Trace 系统测试。
覆盖 TraceRecorder 记录逻辑与 /api/v1/traces 查询接口。
"""


import pytest
from fastapi.testclient import TestClient

from app.config.settings import Settings
from app.queue.base import TaskMessage
from app.queue.memory_queue import InMemoryTaskQueue
from app.services.agent_service import AgentService
from app.services.task_service import TaskService
from app.tracing.recorder import get_trace_recorder


class TestTraceRecorder:
    """Trace 记录器单元测试。"""

    def setup_method(self):
        get_trace_recorder().clear()

    def test_run_lifecycle(self):
        """start_run -> 节点 span -> 工具事件 -> finish_run 全链路可查询。"""
        recorder = get_trace_recorder()
        recorder.start_run("t-1", "目标A", user_id="alice", tenant_id="ten-a")
        recorder.add_node_span(
            "t-1", "planner", started_at=0.0, duration_ms=100.0,
            llm_calls=1, prompt_tokens=10, completion_tokens=5, cost_usd=0.0001,
        )
        recorder.record_tool_call("t-1", "sql_query", allowed=True, latency_ms=20.0)
        recorder.record_tool_call(
            "t-1", "web_search", allowed=False, latency_ms=1.0, reason="审批被拒绝"
        )
        recorder.record_run_usage("t-1", llm_calls=3, prompt_tokens=30,
                                  completion_tokens=15, cost_usd=0.0003)
        recorder.finish_run("t-1", status="completed")

        trace = recorder.get_trace("t-1")
        assert trace is not None
        assert trace.status == "completed"
        assert trace.user_id == "alice"
        assert trace.tenant_id == "ten-a"
        assert len(trace.nodes) == 1
        assert trace.nodes[0].name == "planner"
        assert trace.total_tokens == 45
        assert len(trace.tool_calls) == 2
        assert trace.tool_calls[1].allowed is False
        assert trace.duration_ms >= 0

    def test_record_unknown_task_is_noop(self):
        """未注册任务的记录调用为静默空操作。"""
        recorder = get_trace_recorder()
        recorder.add_node_span("ghost", "planner", 0.0, 1.0)
        recorder.record_tool_call("ghost", "x", True)
        recorder.finish_run("ghost", "completed")
        assert recorder.get_trace("ghost") is None

    def test_bounded_ring_buffer(self):
        """超过容量后最早的记录被裁剪。"""
        recorder = get_trace_recorder()
        for i in range(510):
            recorder.start_run(f"t-{i}", f"goal{i}")
        assert recorder.get_trace("t-0") is None
        assert recorder.get_trace("t-509") is not None

    def test_list_and_clear(self):
        """list_traces 返回最近记录，clear 清空。"""
        recorder = get_trace_recorder()
        recorder.start_run("a", "g1")
        recorder.start_run("b", "g2")
        recorder.finish_run("a", "completed")
        traces = recorder.list_traces(limit=10)
        assert len(traces) == 2
        recorder.clear()
        assert recorder.list_traces() == []

    def test_to_dict_shape(self):
        """to_dict 输出前端可消费的字段（节点时间线/用量/成本）。"""
        recorder = get_trace_recorder()
        recorder.start_run("t-x", "目标", user_id="u", tenant_id="t")
        recorder.add_node_span("t-x", "executor", started_at=1.0,
                               duration_ms=200.0, llm_calls=2)
        data = recorder.get_trace("t-x").to_dict()
        assert data["task_id"] == "t-x"
        assert "nodes" in data and data["nodes"][0]["name"] == "executor"
        assert "tool_calls" in data
        assert "cost_usd" in data
        assert "total_tokens" in data


class TestTraceAPI:
    """Trace 查询接口（离线，内存队列 + 内嵌 Worker 关闭）。"""

    @pytest.mark.asyncio
    async def test_worker_execution_writes_trace(
        self, monkeypatch
    ):
        """
        端到端：入队 -> QueueWorker.process_one 执行 -> Trace 可查询，
        且节点时间线与用量被记录（离线假 LLM）。
        """
        from app.worker import QueueWorker
        from tests.test_budget import make_fake_provider

        task_service = TaskService(Settings(TASK_STORAGE_BACKEND="memory"))
        settings = Settings(
            TASK_STORAGE_BACKEND="memory",
            CHECKPOINT_BACKEND="memory",
            MAX_REPLAN_ITERATIONS=2,
        )

        from app.prompts.manager import PromptManager

        PromptManager.init_defaults()

        class _FakeFactory:
            @staticmethod
            def create(*args, **kwargs):
                return make_fake_provider(
                    '{"goal": "t", "subtasks": [{"id": "task_1", '
                    '"description": "test task", "dependencies": [], "tool": null}]}'
                )

        import app.services.agent_service as agent_service_module

        monkeypatch.setattr(agent_service_module, "LLMProviderFactory", _FakeFactory)

        service = AgentService(task_service=task_service, settings=settings)
        queue = InMemoryTaskQueue()
        worker = QueueWorker(queue, service, settings)

        task_id = await task_service.create_task("trace 集成目标")
        await queue.enqueue(TaskMessage(task_id=task_id, goal="trace 集成目标"))
        processed = await worker.process_one()
        assert processed is True

        recorder = get_trace_recorder()
        trace = recorder.get_trace(task_id)
        assert trace is not None
        assert trace.status in ("completed", "failed")
        names = [n.name for n in trace.nodes]
        assert "planner" in names and "executor" in names
        assert trace.llm_calls >= 1

    def test_get_trace_via_api(self):
        """GET /traces/{task_id} 返回记录；未知任务返回 404。"""
        from main import app

        recorder = get_trace_recorder()
        recorder.start_run("api-trace-1", "接口目标", user_id="u", tenant_id="t")
        recorder.add_node_span("api-trace-1", "planner", started_at=0.0,
                               duration_ms=10.0, llm_calls=1)
        recorder.finish_run("api-trace-1", "completed")

        with TestClient(app) as client:
            resp = client.get("/api/v1/traces/api-trace-1")
            assert resp.status_code == 200
            data = resp.json()
            assert data["task_id"] == "api-trace-1"
            assert data["status"] == "completed"
            assert data["nodes"][0]["name"] == "planner"
            assert data["total_tokens"] >= 0

            missing = client.get("/api/v1/traces/no-such-task")
            assert missing.status_code == 404

    def test_list_traces_via_api(self):
        """GET /traces 返回最近轨迹列表。"""
        from main import app

        recorder = get_trace_recorder()
        recorder.start_run("list-trace-1", "目标1")
        recorder.finish_run("list-trace-1", "completed")
        recorder.start_run("list-trace-2", "目标2")
        recorder.finish_run("list-trace-2", "failed", error="boom")

        with TestClient(app) as client:
            resp = client.get("/api/v1/traces?limit=10")
            assert resp.status_code == 200
            data = resp.json()
            assert data["total"] >= 2
            ids = [t["task_id"] for t in data["traces"]]
            assert "list-trace-1" in ids
            assert "list-trace-2" in ids
            by_id = {t["task_id"]: t for t in data["traces"]}
            assert by_id["list-trace-2"]["error"] == "boom"
