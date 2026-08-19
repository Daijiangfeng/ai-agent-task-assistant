"""
任务模板 / Agent Skill 测试。

覆盖：
- AgentTemplate 模型：变量提取、渲染（{var} 替换、缺失变量留空）；
- TemplateService：内置模板种子、自定义模板 CRUD（内置禁改禁删）；
- 模板创建任务：渲染 + 入队（auto_execute）；
- API：列表/详情/创建/更新/删除/运行。
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_task_service
from app.config.settings import Settings
from app.models.template import AgentTemplate
from app.services.task_service import TaskService
from app.services.template_service import TemplateService, get_template_service
from main import app


@pytest.fixture
def template_service() -> TemplateService:
    """隔离的模板服务实例（不受全局单例污染）。"""
    return TemplateService()


@pytest.fixture
def memory_task_service():
    svc = TaskService(Settings(TASK_STORAGE_BACKEND="memory"))
    app.dependency_overrides[get_task_service] = lambda: svc
    try:
        yield svc
    finally:
        app.dependency_overrides.pop(get_task_service, None)


@pytest.fixture
def client():
    return TestClient(app)


class TestAgentTemplateModel:
    """模板模型：变量提取与渲染。"""

    def test_variables_extracted(self):
        template = AgentTemplate(
            id="t1",
            name="调研",
            goal_template="调研 {topic} 与 {aspect}",
            context_template="语言：{language}",
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-01T00:00:00Z",
        )
        assert template.variables() == ["topic", "aspect", "language"]

    def test_variables_deduplicated_and_ordered(self):
        template = AgentTemplate(
            id="t2",
            name="通用",
            goal_template="{goal} {goal} 再来一次 {context}",
            created_at="",
            updated_at="",
        )
        assert template.variables() == ["goal", "context"]

    def test_render_substitutes_variables(self):
        template = AgentTemplate(
            id="t3",
            name="调研",
            goal_template="调研 {topic}，关注 {aspect}",
            context_template="语言：{language}",
            created_at="",
            updated_at="",
        )
        goal, context = template.render(
            {"topic": "AI 行业", "aspect": "竞争格局", "language": "中文"}
        )
        assert goal == "调研 AI 行业，关注 竞争格局"
        assert context == "语言：中文"

    def test_render_missing_variables_blank(self):
        """缺失变量替换为空字符串，不抛错（保留位置供后续补充）。"""
        template = AgentTemplate(
            id="t4",
            name="调研",
            goal_template="调研 {topic}",
            created_at="",
            updated_at="",
        )
        goal, context = template.render({})
        assert goal == "调研"

    def test_render_no_context_template(self):
        template = AgentTemplate(
            id="t5",
            name="通用",
            goal_template="{goal}",
            created_at="",
            updated_at="",
        )
        goal, context = template.render({"goal": "写报告"})
        assert goal == "写报告"
        assert context is None


class TestTemplateService:
    """模板服务：种子与 CRUD。"""

    def test_builtin_templates_seeded(self, template_service):
        ids = {t.id for t in template_service.list_templates()}
        assert {"market_research", "document_analysis", "code_review", "general"} <= ids

    def test_builtin_has_variables(self, template_service):
        t = template_service.get_template("market_research")
        assert t is not None
        assert {"topic", "aspect"} <= set(t.variables())
        assert t.is_builtin is True

    def test_list_filter_by_category(self, template_service):
        templates = template_service.list_templates(category="market_research")
        assert templates and all(t.category == "market_research" for t in templates)

    def test_create_custom_template(self, template_service):
        from app.models.api_schemas import AgentTemplateCreate

        t = template_service.create_template(
            AgentTemplateCreate(
                name="会议纪要",
                description="会议记录整理",
                category="general",
                goal_template="整理会议纪要：{meeting}",
                context_template=None,
                tags=["会议"],
            )
        )
        assert t.id.startswith("custom_")
        assert t.is_builtin is False
        assert template_service.get_template(t.id) is not None

    def test_update_custom_template(self, template_service):
        from app.models.api_schemas import AgentTemplateCreate, AgentTemplateUpdate

        t = template_service.create_template(
            AgentTemplateCreate(name="A", goal_template="{x}")
        )
        updated = template_service.update_template(
            t.id, AgentTemplateUpdate(name="A2", goal_template="{x} {y}")
        )
        assert updated is not None
        assert updated.name == "A2"
        assert updated.variables() == ["x", "y"]

    def test_builtin_template_cannot_be_updated_or_deleted(self, template_service):
        from app.models.api_schemas import AgentTemplateUpdate

        assert template_service.update_template(
            "general", AgentTemplateUpdate(name="改名")
        ) is None
        assert template_service.delete_template("general") is False

    def test_delete_custom_template(self, template_service):
        from app.models.api_schemas import AgentTemplateCreate

        t = template_service.create_template(
            AgentTemplateCreate(name="临时", goal_template="{x}")
        )
        assert template_service.delete_template(t.id) is True
        assert template_service.get_template(t.id) is None

    @pytest.mark.asyncio
    async def test_create_task_from_template(self, template_service):
        """渲染模板并创建任务。"""
        task_service = TaskService(Settings(TASK_STORAGE_BACKEND="memory"))
        inputs = {
            "topic": "AI",
            "aspect": "趋势",
            "background": "无",
            "focus": "市场",
            "language": "中文",
        }
        task_id = await template_service.create_task_from_template(
            "market_research", inputs, task_service
        )
        task = await task_service.get_task(task_id)
        assert task is not None
        assert "AI" in task.goal
        assert "趋势" in task.goal
        assert task.context and "市场" in task.context

    @pytest.mark.asyncio
    async def test_create_task_missing_template_raises(self, template_service):
        with pytest.raises(ValueError):
            await template_service.create_task_from_template(
                "not_exist", {}, TaskService(Settings(TASK_STORAGE_BACKEND="memory"))
            )

    @pytest.mark.asyncio
    async def test_create_task_blank_goal_raises(self, template_service):
        """变量全部缺失导致目标为空时拒绝创建。"""
        with pytest.raises(ValueError, match="变量缺失"):
            await template_service.create_task_from_template(
                "general", {}, TaskService(Settings(TASK_STORAGE_BACKEND="memory"))
            )


class TestTemplateAPI:
    """模板 API 端点测试。"""

    def _override_service(self, template_service):
        app.dependency_overrides[get_template_service] = lambda: template_service

    def test_list_templates(self, client, template_service):
        self._override_service(template_service)
        resp = client.get("/api/v1/templates/")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 4
        names = {t["name"] for t in data["templates"]}
        assert {"市场调研", "文档分析", "代码审查", "通用任务"} <= names

    def test_get_template_with_variables(self, client, template_service):
        self._override_service(template_service)
        resp = client.get("/api/v1/templates/market_research")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "market_research"
        assert "topic" in data["variables"]

    def test_get_missing_template_404(self, client, template_service):
        self._override_service(template_service)
        resp = client.get("/api/v1/templates/nope")
        assert resp.status_code == 404

    def test_create_template_api(self, client, template_service):
        self._override_service(template_service)
        resp = client.post(
            "/api/v1/templates/",
            json={
                "name": "招聘分析",
                "description": "分析岗位要求",
                "category": "general",
                "goal_template": "分析 {role} 岗位要求",
                "tags": ["招聘"],
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["id"].startswith("custom_")
        assert data["variables"] == ["role"]

    def test_update_custom_template_api(self, client, template_service):
        self._override_service(template_service)
        created = client.post(
            "/api/v1/templates/",
            json={"name": "A", "goal_template": "{x}"},
        ).json()
        resp = client.put(
            f"/api/v1/templates/{created['id']}",
            json={"name": "A2"},
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "A2"

    def test_update_builtin_template_404(self, client, template_service):
        self._override_service(template_service)
        resp = client.put("/api/v1/templates/general", json={"name": "x"})
        assert resp.status_code == 404

    def test_delete_custom_template_api(self, client, template_service):
        self._override_service(template_service)
        created = client.post(
            "/api/v1/templates/",
            json={"name": "临时", "goal_template": "{x}"},
        ).json()
        resp = client.delete(f"/api/v1/templates/{created['id']}")
        assert resp.status_code == 204

    def test_delete_builtin_template_404(self, client, template_service):
        self._override_service(template_service)
        resp = client.delete("/api/v1/templates/general")
        assert resp.status_code == 404

    def test_run_template_creates_task(self, client, template_service, memory_task_service):
        """运行模板：渲染变量创建任务（不自动执行）。"""
        self._override_service(template_service)
        resp = client.post(
            "/api/v1/templates/market_research/run",
            json={
                "inputs": {
                    "topic": "AI 行业",
                    "aspect": "趋势",
                    "background": "无",
                    "focus": "市场",
                    "language": "中文",
                },
                "auto_execute": False,
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "pending"
        task = asyncio.run(memory_task_service.get_task(data["task_id"]))
        assert task is not None
        assert "AI 行业" in task.goal
        assert "趋势" in task.goal

    def test_run_template_missing_variables_still_creates(
        self, client, template_service, memory_task_service
    ):
        """变量缺失（空串）不阻塞创建（通用模板 goal={goal} 为空时拒绝）。"""
        self._override_service(template_service)
        resp = client.post(
            "/api/v1/templates/general/run",
            json={"inputs": {"goal": "完成市场分析报告"}},
        )
        assert resp.status_code == 201
        task = asyncio.run(memory_task_service.get_task(resp.json()["task_id"]))
        assert "市场分析报告" in task.goal

    def test_run_missing_template_404(self, client, template_service, memory_task_service):
        self._override_service(template_service)
        resp = client.post("/api/v1/templates/nope/run", json={"inputs": {}})
        assert resp.status_code == 404

    def test_run_template_blank_goal_400(self, client, template_service, memory_task_service):
        """通用模板无 goal 输入 -> 渲染为空 -> 400。"""
        self._override_service(template_service)
        resp = client.post("/api/v1/templates/general/run", json={"inputs": {}})
        assert resp.status_code == 400

    def test_run_template_auto_execute_enqueues(
        self, client, template_service, memory_task_service
    ):
        """auto_execute=true 时创建后立即入队。"""

        self._override_service(template_service)
        with patch(
            "app.queue.memory_queue.InMemoryTaskQueue.enqueue",
            new_callable=AsyncMock,
        ) as mock_enqueue:
            resp = client.post(
                "/api/v1/templates/general/run",
                json={"inputs": {"goal": "执行吧"}, "auto_execute": True},
            )
        assert resp.status_code == 201
        assert mock_enqueue.called
        msg = mock_enqueue.call_args.args[0]
        assert msg.task_id == resp.json()["task_id"]
