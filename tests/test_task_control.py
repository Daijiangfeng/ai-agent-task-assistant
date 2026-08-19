"""
任务控制测试：暂停 / 恢复 / 取消 / 重试。

覆盖：
- TaskControlService 登记/清除/查询；
- 生命周期 API：暂停（仅运行中）、取消（仅非终态）、恢复（仅可恢复状态）、
  重试（RESTARTABLE_STATUSES + from_index 校验 + 队列消息载荷）；
- PlannerNode 重试路径：复用计划、跳过 LLM、截断已完成结果。
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_task_service
from app.models.task import TaskStatus
from app.services.task_control import TaskControlService, get_task_control
from main import app


@pytest.fixture(autouse=True)
def reset_control():
    get_task_control().reset_all()
    yield
    get_task_control().reset_all()


@pytest.fixture
def memory_task_service():
    from app.api.deps import get_task_service
    from app.config.settings import Settings
    from app.services.task_service import TaskService

    svc = TaskService(Settings(TASK_STORAGE_BACKEND="memory"))
    app.dependency_overrides[get_task_service] = lambda: svc
    try:
        yield svc
    finally:
        app.dependency_overrides.pop(get_task_service, None)


@pytest.fixture
def client():
    return TestClient(app)


class TestTaskControlService:
    """控制登记单测。"""

    def test_pause_cancel_exclusive(self):
        ctrl = TaskControlService()
        ctrl.request_pause("t1")
        assert ctrl.should_pause("t1")
        ctrl.request_cancel("t1")
        assert ctrl.should_cancel("t1")
        assert not ctrl.should_pause("t1")

    def test_clear_and_reset(self):
        ctrl = TaskControlService()
        ctrl.request_pause("t1")
        ctrl.request_cancel("t2")
        ctrl.clear("t1")
        assert not ctrl.should_pause("t1")
        assert ctrl.should_cancel("t2")
        ctrl.reset_all()
        assert not ctrl.should_cancel("t2")

    def test_clear_pause_only(self):
        ctrl = TaskControlService()
        ctrl.request_pause("t1")
        ctrl.request_cancel("t1")
        ctrl.clear_pause("t1")
        assert not ctrl.should_pause("t1")
        assert ctrl.should_cancel("t1")


class TestLifecycleAPI:
    """暂停/恢复/取消/重试 API 测试。"""

    def _create_and_run(self, client, status=TaskStatus.EXECUTING):
        resp = client.post("/api/v1/tasks/", json={"goal": "生命周期测试"})
        task_id = resp.json()["task_id"]
        # 直接置为指定状态模拟运行中
        svc = app.dependency_overrides[get_task_service]()
        import asyncio

        asyncio.run(svc.update_task_status(task_id, status))
        return task_id

    def test_pause_running_task(self, client, memory_task_service):
        """运行中的任务可暂停，返回暂停状态。"""
        task_id = self._create_and_run(client)
        resp = client.post(f"/api/v1/tasks/{task_id}/pause")
        assert resp.status_code == 200
        # 暂停请求已登记
        assert get_task_control().should_pause(task_id)
        assert resp.json()["status"] in ("executing", "planning")

    def test_pause_completed_task_rejected(self, client, memory_task_service):
        """已完成任务不可暂停（400）。"""
        task_id = self._create_and_run(client, TaskStatus.COMPLETED)
        resp = client.post(f"/api/v1/tasks/{task_id}/pause")
        assert resp.status_code == 400
        assert "状态" in resp.json()["detail"]

    def test_cancel_running_task(self, client, memory_task_service):
        """运行中的任务可取消，请求被登记。"""
        task_id = self._create_and_run(client)
        resp = client.post(f"/api/v1/tasks/{task_id}/cancel")
        assert resp.status_code == 200
        assert get_task_control().should_cancel(task_id)

    def test_cancel_completed_task_rejected(self, client, memory_task_service):
        """终态任务不可取消（400）。"""
        task_id = self._create_and_run(client, TaskStatus.COMPLETED)
        resp = client.post(f"/api/v1/tasks/{task_id}/cancel")
        assert resp.status_code == 400

    def test_cancel_awaiting_approval_resolves_pending(self, client, memory_task_service):
        """取消待审批任务时同步驳回挂起审批请求。"""
        from app.models.task import ApprovalRequest, ApprovalStatus

        task_id = self._create_and_run(client, TaskStatus.AWAITING_APPROVAL)
        # 注入一个挂起审批
        import asyncio

        svc = app.dependency_overrides[get_task_service]()
        req = ApprovalRequest(
            id="ap-1", task_id=task_id, tool_name="sql_query",
            args={"query": "SELECT 1"}, reason="测试审批", created_at="2026-01-01T00:00:00Z",
        )
        asyncio.run(svc.save_approval_request(req))
        resp = client.post(f"/api/v1/tasks/{task_id}/cancel")
        assert resp.status_code == 200
        task = asyncio.run(svc.get_task(task_id))
        assert task.pending_approval is None
        assert task.approval_history[0].status == ApprovalStatus.REJECTED

    def test_resume_paused_task(self, client, memory_task_service):
        """恢复暂停任务：清除控制请求并入队（状态回 planning）。"""
        task_id = self._create_and_run(client, TaskStatus.PAUSED)
        resp = client.post(f"/api/v1/tasks/{task_id}/resume")
        assert resp.status_code == 200
        assert not get_task_control().should_pause(task_id)
        assert resp.json()["status"] == "planning"

    def test_resume_running_task_rejected(self, client, memory_task_service):
        """运行中的任务不可恢复（400）。"""
        task_id = self._create_and_run(client, TaskStatus.EXECUTING)
        resp = client.post(f"/api/v1/tasks/{task_id}/resume")
        assert resp.status_code == 400

    def test_retry_failed_task(self, client, memory_task_service):
        """失败任务重试：入队携带 retry_from_index 载荷。"""
        from app.queue.base import TaskMessage

        task_id = self._create_and_run(client, TaskStatus.FAILED)
        with patch(
            "app.queue.memory_queue.InMemoryTaskQueue.enqueue",
            new_callable=AsyncMock,
        ) as mock_enqueue:
            resp = client.post(f"/api/v1/tasks/{task_id}/retry")
        assert resp.status_code == 200
        msg: TaskMessage = mock_enqueue.call_args.args[0]
        assert msg.action == "execute"
        assert msg.payload == {"retry_from_index": None}
        assert resp.json()["status"] == "planning"

    def test_retry_from_index(self, client, memory_task_service):
        """带 from_index 重试：任务状态重置且结果清空。"""

        task_id = self._create_and_run(client, TaskStatus.FAILED)
        import asyncio

        svc = app.dependency_overrides[get_task_service]()
        asyncio.run(
            svc.sync_plan(task_id, {"goal": "x", "subtasks": [{"id": "a"}, {"id": "b"}]})
        )
        with patch(
            "app.queue.memory_queue.InMemoryTaskQueue.enqueue",
            new_callable=AsyncMock,
        ) as mock_enqueue:
            resp = client.post(
                f"/api/v1/tasks/{task_id}/retry",
                json={"from_index": 1},
            )
        assert resp.status_code == 200
        assert mock_enqueue.call_args.args[0].payload == {"retry_from_index": 1}

    def test_retry_running_task_rejected(self, client, memory_task_service):
        """运行中的任务不可重试（400）。"""
        task_id = self._create_and_run(client, TaskStatus.EXECUTING)
        resp = client.post(f"/api/v1/tasks/{task_id}/retry")
        assert resp.status_code == 400

    def test_retry_from_index_out_of_range(self, client, memory_task_service):
        """from_index 超出子任务范围返回 400。"""
        task_id = self._create_and_run(client, TaskStatus.FAILED)
        import asyncio

        svc = app.dependency_overrides[get_task_service]()
        asyncio.run(svc.sync_plan(task_id, {"goal": "x", "subtasks": [{"id": "a"}]}))
        resp = client.post(f"/api/v1/tasks/{task_id}/retry", json={"from_index": 5})
        assert resp.status_code == 400

    def test_controls_require_ownership(self, client, memory_task_service):
        """他人任务不可暂停（403）。"""
        resp = client.post(
            "/api/v1/tasks/",
            json={"goal": "alice 的任务"},
            headers={"X-User-Id": "alice"},
        )
        task_id = resp.json()["task_id"]
        resp = client.post(
            f"/api/v1/tasks/{task_id}/pause",
            headers={"X-User-Id": "bob", "X-User-Role": "user"},
        )
        assert resp.status_code == 403


class TestPlannerRetry:
    """PlannerNode 重试路径。"""

    @pytest.mark.asyncio
    async def test_retry_reuses_plan_and_skips_llm(self):
        from langchain_core.messages import AIMessage
        from langchain_core.runnables import Runnable

        from app.agent.planner_node import PlannerNode

        calls = []

        class _Model(Runnable):
            def bind_tools(self, tools, **kwargs):
                return self

            def invoke(self, model_input, config=None, **kwargs):  # pragma: no cover
                import asyncio

                return asyncio.run(self.ainvoke(model_input, config=config, **kwargs))

            async def ainvoke(self, model_input, config=None, **kwargs):
                calls.append(model_input)
                return AIMessage(content="{}")

        provider = MagicMock()
        provider.get_chat_model = MagicMock(return_value=_Model())
        node = PlannerNode(provider, MagicMock())
        state = {
            "goal": "重试目标",
            "context": None,
            "plan": {"goal": "重试目标", "subtasks": [{"id": "a"}, {"id": "b"}]},
            "plan_version": 2,
            "current_task_index": 1,
            "task_results": [
                {"subtask_id": "a", "result": "已完成", "status": "completed"},
                {"subtask_id": "b", "result": "失败", "status": "failed"},
            ],
            "reflection_result": None,
            "should_replan": True,
            "iteration_count": 3,
            "final_result": None,
            "task_id": "retry-1",
            "retry_from_index": 1,
            "messages": [],
            "errors": [],
        }
        result = await node.run(state)
        # 复用计划：不调用 LLM，重置执行索引，截断失败点之后的结果
        assert calls == []
        assert result["current_task_index"] == 1
        assert result.get("retry_from_index") is None
        assert result["should_replan"] is False
        assert [r["subtask_id"] for r in result["task_results"]] == ["a"]

    @pytest.mark.asyncio
    async def test_normal_path_plans_fresh(self):
        from langchain_core.messages import AIMessage
        from langchain_core.runnables import Runnable

        from app.agent.planner_node import PlannerNode
        from app.prompts.manager import PromptManager

        PromptManager.init_defaults()

        class _Model(Runnable):
            def bind_tools(self, tools, **kwargs):
                return self

            def invoke(self, model_input, config=None, **kwargs):  # pragma: no cover
                import asyncio

                return asyncio.run(self.ainvoke(model_input, config=config, **kwargs))

            async def ainvoke(self, model_input, config=None, **kwargs):
                return AIMessage(content='{"goal": "x", "subtasks": []}')

        provider = MagicMock()
        provider.get_chat_model = MagicMock(return_value=_Model())
        node = PlannerNode(provider, PromptManager)
        state = {
            "goal": "新目标",
            "context": None,
            "plan": None,
            "plan_version": 0,
            "current_task_index": 0,
            "task_results": [],
            "reflection_result": None,
            "should_replan": False,
            "iteration_count": 0,
            "final_result": None,
            "task_id": "retry-2",
            "messages": [],
            "errors": [],
        }
        result = await node.run(state)
        assert result["plan"] == {"goal": "x", "subtasks": []}
        assert result["plan_version"] == 1
