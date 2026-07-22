"""
API 端点集成测试。
使用 httpx TestClient 测试 FastAPI 路由。
"""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from main import app


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
