"""
API 端点集成测试。
使用 httpx TestClient 测试 FastAPI 路由。
"""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from main import app


@pytest.fixture(autouse=True)
def memory_task_service():
    """
    将 TaskService 依赖固定为内存后端。

    默认后端为 auto（PostgreSQL 优先），本机若运行 PostgreSQL 会导致
    API 测试读写真实数据库，破坏测试隔离；内存后端保证测试确定性且离线可跑。
    """
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
    """创建测试客户端。"""
    return TestClient(app)


class TestHealthCheck:
    """健康检查接口测试。"""

    def test_health_check(self, client: TestClient):
        """测试健康检查返回正常。"""
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert "version" in data


class TestTaskAPI:
    """任务 API 测试。"""

    def test_create_task(self, client: TestClient):
        """测试创建任务。"""
        response = client.post(
            "/api/v1/tasks/",
            json={"goal": "帮我分析 AI 发展趋势", "context": "关注大模型领域"},
        )
        assert response.status_code == 201
        data = response.json()
        assert "task_id" in data
        assert data["status"] == "pending"
        assert "created_at" in data

    def test_create_task_without_context(self, client: TestClient):
        """测试创建任务（无上下文）。"""
        response = client.post(
            "/api/v1/tasks/",
            json={"goal": "总结今日新闻"},
        )
        assert response.status_code == 201
        data = response.json()
        assert data["status"] == "pending"

    def test_create_task_missing_goal(self, client: TestClient):
        """测试缺少 goal 字段时返回 422。"""
        response = client.post(
            "/api/v1/tasks/",
            json={"context": "some context"},
        )
        assert response.status_code == 422

    def test_list_tasks(self, client: TestClient):
        """测试列表查询任务。"""
        # 先创建一个任务
        client.post("/api/v1/tasks/", json={"goal": "测试任务1"})
        client.post("/api/v1/tasks/", json={"goal": "测试任务2"})

        response = client.get("/api/v1/tasks/")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] >= 2
        assert len(data["tasks"]) >= 2

    def test_list_tasks_with_pagination(self, client: TestClient):
        """测试分页参数。"""
        response = client.get("/api/v1/tasks/?limit=1&offset=0")
        assert response.status_code == 200
        data = response.json()
        assert len(data["tasks"]) <= 1

    def test_get_task_status(self, client: TestClient):
        """测试查询任务状态。"""
        # 先创建任务
        create_response = client.post(
            "/api/v1/tasks/",
            json={"goal": "查询状态测试"},
        )
        task_id = create_response.json()["task_id"]

        # 查询状态
        response = client.get(f"/api/v1/tasks/{task_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["task_id"] == task_id
        assert data["status"] == "pending"
        assert "progress" in data

    def test_get_nonexistent_task(self, client: TestClient):
        """测试查询不存在的任务返回 404。"""
        response = client.get("/api/v1/tasks/nonexistent-id")
        assert response.status_code == 404

    @patch(
        "app.services.agent_service.AgentService.run_task",
        new_callable=AsyncMock,
        return_value="mock result",
    )
    def test_execute_task(self, mock_run, client: TestClient):
        """测试启动任务执行。"""
        # 先创建任务
        create_response = client.post(
            "/api/v1/tasks/",
            json={"goal": "执行测试任务"},
        )
        task_id = create_response.json()["task_id"]

        # 启动执行
        response = client.post(f"/api/v1/tasks/{task_id}/execute")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "planning"

    def test_execute_nonexistent_task(self, client: TestClient):
        """测试执行不存在的任务返回 404。"""
        response = client.post("/api/v1/tasks/nonexistent-id/execute")
        assert response.status_code == 404

    @patch(
        "app.services.agent_service.AgentService.run_task",
        new_callable=AsyncMock,
        return_value="mock result",
    )
    def test_execute_already_running_task(self, mock_run, client: TestClient):
        """测试执行已在运行中的任务返回 400。"""
        # 创建任务
        create_response = client.post(
            "/api/v1/tasks/",
            json={"goal": "重复执行测试"},
        )
        task_id = create_response.json()["task_id"]

        # 第一次执行
        client.post(f"/api/v1/tasks/{task_id}/execute")

        # 再次执行应返回 400
        response = client.post(f"/api/v1/tasks/{task_id}/execute")
        assert response.status_code == 400


class TestAPIAuthentication:
    """API 认证与授权测试（AUTH_ENABLED 生产模式）。"""

    def _enable_auth(self, monkeypatch, keys: str = "secret-key-1"):
        from app.api import auth as auth_module

        monkeypatch.setattr(
            auth_module,
            "get_settings",
            lambda: type(
                "S", (), {"AUTH_ENABLED": True, "API_KEYS": keys}
            )(),
        )

    def test_missing_api_key_rejected(self, client: TestClient, monkeypatch):
        """启用认证后，无 API Key 的请求返回 401。"""
        self._enable_auth(monkeypatch)
        response = client.get("/api/v1/tools")
        assert response.status_code == 401

    def test_invalid_api_key_rejected(self, client: TestClient, monkeypatch):
        """错误的 API Key 返回 401。"""
        self._enable_auth(monkeypatch)
        response = client.get(
            "/api/v1/tools", headers={"X-API-Key": "wrong-key"}
        )
        assert response.status_code == 401

    def test_valid_api_key_allowed(self, client: TestClient, monkeypatch):
        """有效的 API Key（Bearer 头）放行。"""
        self._enable_auth(monkeypatch)
        response = client.get(
            "/api/v1/tools", headers={"Authorization": "Bearer secret-key-1"}
        )
        assert response.status_code == 200

    def test_auth_disabled_default_pass(self, client: TestClient):
        """未启用认证（开发模式）默认放行。"""
        response = client.get("/api/v1/tools")
        assert response.status_code == 200


class TestTaskOwnership:
    """资源所有权检查（Resource Ownership Check）测试。"""

    def _create_task_as(self, client: TestClient, user_id: str) -> str:
        resp = client.post(
            "/api/v1/tasks/",
            json={"goal": f"{user_id} 的任务"},
            headers={"X-User-Id": user_id},
        )
        assert resp.status_code == 201
        return resp.json()["task_id"]

    def test_owner_can_access_own_task(self, client: TestClient):
        """任务所有者可以查询自己的任务。"""
        task_id = self._create_task_as(client, "alice")
        response = client.get(
            f"/api/v1/tasks/{task_id}", headers={"X-User-Id": "alice"}
        )
        assert response.status_code == 200
        assert response.json()["task_id"] == task_id

    def test_other_user_forbidden(self, client: TestClient):
        """其他用户访问他人任务返回 403。"""
        task_id = self._create_task_as(client, "alice")
        response = client.get(
            f"/api/v1/tasks/{task_id}",
            headers={"X-User-Id": "bob", "X-User-Role": "user"},
        )
        assert response.status_code == 403

    def test_admin_can_access_any_task(self, client: TestClient):
        """admin 角色可访问任意任务。"""
        task_id = self._create_task_as(client, "alice")
        response = client.get(
            f"/api/v1/tasks/{task_id}",
            headers={"X-User-Id": "root", "X-User-Role": "admin"},
        )
        assert response.status_code == 200

    def test_guest_cannot_execute_others_task(self, client: TestClient):
        """guest 角色访问他人任务返回 403。"""
        task_id = self._create_task_as(client, "alice")
        response = client.get(
            f"/api/v1/tasks/{task_id}",
            headers={"X-User-Id": "guest-user", "X-User-Role": "guest"},
        )
        assert response.status_code == 403

    def test_same_user_different_tenant_forbidden(self, client: TestClient):
        """同一用户跨租户访问他人租户任务返回 403（多租户隔离）。"""
        resp = client.post(
            "/api/v1/tasks/",
            json={"goal": "租户A的任务"},
            headers={"X-User-Id": "alice", "X-Tenant-Id": "tenant_a"},
        )
        task_id = resp.json()["task_id"]
        response = client.get(
            f"/api/v1/tasks/{task_id}",
            headers={
                "X-User-Id": "alice",
                "X-Tenant-Id": "tenant_b",
                "X-User-Role": "user",
            },
        )
        assert response.status_code == 403

    def test_task_list_isolated_by_tenant_and_user(self, client: TestClient):
        """任务列表按租户+用户过滤：普通用户看不到其他租户/用户的任务。"""
        client.post(
            "/api/v1/tasks/",
            json={"goal": "租户A的任务"},
            headers={"X-User-Id": "alice", "X-Tenant-Id": "tenant_a"},
        )
        client.post(
            "/api/v1/tasks/",
            json={"goal": "租户B的任务"},
            headers={"X-User-Id": "alice", "X-Tenant-Id": "tenant_b"},
        )
        resp = client.get(
            "/api/v1/tasks/",
            headers={
                "X-User-Id": "alice",
                "X-Tenant-Id": "tenant_a",
                "X-User-Role": "user",
            },
        )
        data = resp.json()
        assert data["total"] == 1
        assert all(t["task_id"] for t in data["tasks"])

    @patch(
        "app.services.agent_service.AgentService.run_task",
        new_callable=AsyncMock,
        return_value="mock result",
    )
    def test_guest_cannot_execute_task(self, mock_run, client: TestClient):
        """guest 角色不能启动他人任务的执行。"""
        task_id = self._create_task_as(client, "alice")
        response = client.post(
            f"/api/v1/tasks/{task_id}/execute",
            headers={"X-User-Id": "guest-user", "X-User-Role": "guest"},
        )
        assert response.status_code == 403
        mock_run.assert_not_called()
