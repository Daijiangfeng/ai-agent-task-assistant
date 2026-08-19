"""
P0-1 生产安全模式测试。

覆盖：
1. ENVIRONMENT 配置校验与 is_production 判定；
2. 生产模式禁止静默降级：任务存储 / Checkpoint / 队列 / 短期记忆 / Mock 工具；
3. 基础设施健康检查（InfrastructureHealthChecker）与就绪门禁（ReadinessGate）；
4. /health/live 与 /health/ready 端点；
5. require_ready：生产模式未就绪时任务接收端点返回 503；
6. 生产启动 fail-fast（verify_production_readiness）。

全部离线可跑：使用不可达的本地端口（连接拒绝，快速失败）模拟依赖故障。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config.settings import Settings
from app.memory.factory import MemoryFactory
from app.memory.short_term import RedisShortTermMemory
from app.queue.factory import create_task_queue
from app.queue.memory_queue import InMemoryTaskQueue
from app.services.health import (
    ComponentStatus,
    HealthReport,
    InfrastructureHealthChecker,
    ReadinessGate,
    verify_production_readiness,
)
from app.services.task_service import TaskService

# 本机几乎不可能监听的端口，用于模拟基础设施不可达（连接拒绝，快速失败）
UNREACHABLE_PORT = 6390


def prod_settings(**overrides) -> Settings:
    """构造生产模式配置（默认全部依赖指向不可达端口）。"""
    base = dict(
        ENVIRONMENT="production",
        REDIS_PORT=UNREACHABLE_PORT,
        POSTGRES_PORT=UNREACHABLE_PORT,
        DB_CONNECT_TIMEOUT=1,
        HEALTH_CHECK_TIMEOUT=1.0,
    )
    base.update(overrides)
    return Settings(**base)


# ---------------------------------------------------------------------------
# 1. ENVIRONMENT 配置
# ---------------------------------------------------------------------------


class TestEnvironmentSetting:
    """ENVIRONMENT 配置校验。"""

    def test_default_is_development(self):
        assert Settings().ENVIRONMENT == "development"
        assert Settings().is_production is False

    def test_normalizes_case_and_whitespace(self):
        settings = Settings(ENVIRONMENT=" Production ")
        assert settings.ENVIRONMENT == "production"
        assert settings.is_production is True

    def test_invalid_value_rejected(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            Settings(ENVIRONMENT="staging")


# ---------------------------------------------------------------------------
# 2. 生产模式禁止静默降级
# ---------------------------------------------------------------------------


class TestProductionGuards:
    """生产模式下各工厂的禁降级守卫。"""

    def test_queue_memory_forbidden_in_production(self):
        with pytest.raises(RuntimeError, match="内存任务队列"):
            create_task_queue(prod_settings(TASK_QUEUE_BACKEND="memory"))

    def test_queue_auto_no_fallback_in_production(self, monkeypatch):
        """生产 auto 模式 Redis 不可达时抛错（开发模式则降级内存）。"""
        from unittest.mock import AsyncMock

        from app.queue.redis_queue import RedisTaskQueue

        monkeypatch.setattr(RedisTaskQueue, "ping", AsyncMock(return_value=False))

        with pytest.raises(RuntimeError, match="禁止降级内存"):
            create_task_queue(prod_settings(TASK_QUEUE_BACKEND="auto"))

        # 同样配置在开发模式下正常降级
        dev = create_task_queue(
            Settings(TASK_QUEUE_BACKEND="auto", REDIS_PORT=UNREACHABLE_PORT)
        )
        assert isinstance(dev, InMemoryTaskQueue)

    def test_task_storage_memory_forbidden_in_production(self):
        with pytest.raises(RuntimeError, match="内存任务存储"):
            TaskService(prod_settings(TASK_STORAGE_BACKEND="memory"))

    def test_task_storage_auto_no_fallback_in_production(self):
        with pytest.raises(RuntimeError, match="禁止降级内存"):
            service = TaskService(prod_settings(TASK_STORAGE_BACKEND="auto"))
            # auto 为懒探测：首次访问仓库时抛错
            import asyncio

            asyncio.run(service._get_repo())

    def test_checkpoint_memory_forbidden_in_production(self):
        import asyncio

        from app.agent.checkpoint import create_checkpointer

        with pytest.raises(RuntimeError, match="MemorySaver"):
            asyncio.run(create_checkpointer(prod_settings(CHECKPOINT_BACKEND="memory")))

    def test_short_term_memory_strict_in_production(self):
        """生产模式 STM：Redis 不可达时抛错（不降级内存）。"""
        memory = RedisShortTermMemory(prod_settings())
        with pytest.raises(RuntimeError, match="禁止降级内存"):
            import asyncio

            asyncio.run(memory.save("k", "v"))

    def test_memory_factory_memory_forbidden_in_production(self):
        with pytest.raises(RuntimeError, match="短期记忆必须使用 Redis"):
            MemoryFactory.create_short_term(prod_settings(), use_redis=False)

    def test_mock_email_tool_skipped_in_production(self, monkeypatch):
        """生产模式跳过内存 Mock 邮件工具注册（开发模式正常注册）。"""
        from app.config.settings import get_settings
        from app.tools.builtins import register_builtin_tools
        from app.tools.registry import ToolRegistry

        monkeypatch.setenv("ENVIRONMENT", "production")
        get_settings.cache_clear()
        register_builtin_tools()
        assert ToolRegistry.get("email.send") is None
        assert ToolRegistry.get("datetime_tool") is not None

    def test_email_tool_registered_in_development(self, monkeypatch):
        from app.config.settings import get_settings
        from app.tools.builtins import register_builtin_tools
        from app.tools.registry import ToolRegistry

        monkeypatch.setenv("ENVIRONMENT", "development")
        get_settings.cache_clear()
        register_builtin_tools()
        assert ToolRegistry.get("email.send") is not None


# ---------------------------------------------------------------------------
# 3. 健康检查器与就绪门禁
# ---------------------------------------------------------------------------


class TestHealthChecker:
    """InfrastructureHealthChecker 组件探测。"""

    @pytest.mark.asyncio
    async def test_ready_when_all_core_up(self, tmp_path, monkeypatch):
        """开发模式全内存后端 + 本地 chroma + 已配置 LLM 凭证 → ready。"""
        settings = Settings(
            ENVIRONMENT="development",
            TASK_QUEUE_BACKEND="memory",
            TASK_STORAGE_BACKEND="memory",
            CHECKPOINT_BACKEND="memory",
            VECTOR_STORE_BACKEND="chroma",
            CHROMA_PERSIST_DIR=str(tmp_path / "chroma"),
            ANTHROPIC_AUTH_TOKEN="test-key",
            HEALTH_CHECK_TIMEOUT=2.0,
        )
        report = await InfrastructureHealthChecker(settings).check()
        assert report.ready is True
        names = {c.name: c for c in report.components}
        assert names["database"].status == "up"
        assert names["queue"].status == "up"
        assert names["vector_db"].status == "up"
        assert names["llm"].status == "up"
        assert names["storage"].status == "up"
        # 信息性组件允许 down，不阻断就绪
        assert names["redis"].core is False

    @pytest.mark.asyncio
    async def test_not_ready_when_llm_missing(self, tmp_path):
        """LLM 凭证缺失（核心组件）→ ready=False。"""
        settings = Settings(
            ENVIRONMENT="development",
            TASK_QUEUE_BACKEND="memory",
            TASK_STORAGE_BACKEND="memory",
            CHECKPOINT_BACKEND="memory",
            VECTOR_STORE_BACKEND="chroma",
            CHROMA_PERSIST_DIR=str(tmp_path / "chroma"),
            ANTHROPIC_AUTH_TOKEN="",
        )
        report = await InfrastructureHealthChecker(settings).check()
        assert report.ready is False
        assert "llm" in report.failed_core_components()

    @pytest.mark.asyncio
    async def test_not_ready_when_queue_down_in_production(self, tmp_path):
        """生产模式队列不可达（核心组件）→ ready=False。"""
        settings = prod_settings(
            TASK_QUEUE_BACKEND="redis",
            TASK_STORAGE_BACKEND="memory",  # 触发核心 down（生产禁止内存）
            VECTOR_STORE_BACKEND="chroma",
            CHROMA_PERSIST_DIR=str(tmp_path / "chroma"),
            ANTHROPIC_AUTH_TOKEN="test-key",
        )
        report = await InfrastructureHealthChecker(settings).check()
        assert report.ready is False
        failed = report.failed_core_components()
        assert "queue" in failed
        assert "database" in failed

    @pytest.mark.asyncio
    async def test_dev_auto_degradation_not_blocking(self, tmp_path):
        """开发模式 auto 依赖故障为设计内降级（core=False），不阻断就绪。"""
        settings = Settings(
            ENVIRONMENT="development",
            TASK_QUEUE_BACKEND="auto",
            TASK_STORAGE_BACKEND="auto",
            CHECKPOINT_BACKEND="auto",
            VECTOR_STORE_BACKEND="chroma",
            REDIS_HOST="127.0.0.1",
            REDIS_PORT=UNREACHABLE_PORT,
            POSTGRES_HOST="127.0.0.1",
            POSTGRES_PORT=UNREACHABLE_PORT,
            HEALTH_CHECK_TIMEOUT=0.5,
            CHROMA_PERSIST_DIR=str(tmp_path / "chroma"),
            ANTHROPIC_AUTH_TOKEN="test-key",
        )
        report = await InfrastructureHealthChecker(settings).check()
        names = {c.name: c for c in report.components}
        assert names["database"].core is False
        assert names["queue"].core is False
        assert names["checkpoint"].core is False
        assert report.ready is True

    @pytest.mark.asyncio
    async def test_readiness_gate_caches_report(self, tmp_path):
        """ReadinessGate TTL 缓存：窗口内复用同一报告。"""
        settings = Settings(
            ENVIRONMENT="development",
            TASK_QUEUE_BACKEND="memory",
            TASK_STORAGE_BACKEND="memory",
            CHECKPOINT_BACKEND="memory",
            VECTOR_STORE_BACKEND="chroma",
            CHROMA_PERSIST_DIR=str(tmp_path / "chroma"),
            ANTHROPIC_AUTH_TOKEN="test-key",
        )
        gate = ReadinessGate(InfrastructureHealthChecker(settings), ttl_seconds=60)
        first = await gate.get_report()
        second = await gate.get_report()
        assert first is second

    @pytest.mark.asyncio
    async def test_verify_production_readiness_fail_fast(self, tmp_path):
        """生产启动校验：核心组件 down → RuntimeError（fail-fast）。"""
        settings = prod_settings(
            TASK_QUEUE_BACKEND="redis",
            VECTOR_STORE_BACKEND="chroma",
            CHROMA_PERSIST_DIR=str(tmp_path / "chroma"),
            ANTHROPIC_AUTH_TOKEN="test-key",
        )
        with pytest.raises(RuntimeError, match="健康检查未通过"):
            await verify_production_readiness(settings)

    @pytest.mark.asyncio
    async def test_verify_production_readiness_passes(self, monkeypatch):
        """生产启动校验：全部核心组件 up → 返回报告不抛错。"""
        import app.services.health as health_mod

        ready_report = HealthReport(
            ready=True,
            environment="production",
            checked_at="2026-08-19T00:00:00",
            components=[ComponentStatus("queue", "up")],
        )

        class _StubChecker:
            def __init__(self, settings=None):
                pass

            async def check(self) -> HealthReport:
                return ready_report

        monkeypatch.setattr(health_mod, "InfrastructureHealthChecker", _StubChecker)
        report = await health_mod.verify_production_readiness(
            Settings(ENVIRONMENT="production")
        )
        assert report.ready is True


# ---------------------------------------------------------------------------
# 4. 健康端点与任务接收门禁
# ---------------------------------------------------------------------------


class TestHealthEndpoints:
    """/health/live 与 /health/ready 端点。"""

    @pytest.fixture(autouse=True)
    def _reset_gate(self):
        from app.services.health import reset_readiness_gate

        reset_readiness_gate()
        yield
        reset_readiness_gate()

    @pytest.fixture
    def client(self):
        from main import app

        return TestClient(app)

    def test_liveness_always_ok(self, client):
        response = client.get("/health/live")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "alive"
        assert "version" in data

    def test_readiness_ok_when_ready(self, client, monkeypatch, tmp_path):
        """依赖全部就绪 → 200 + ready=true。"""
        from app.config.settings import get_settings

        monkeypatch.setenv("TASK_QUEUE_BACKEND", "memory")
        monkeypatch.setenv("TASK_STORAGE_BACKEND", "memory")
        monkeypatch.setenv("CHECKPOINT_BACKEND", "memory")
        monkeypatch.setenv("VECTOR_STORE_BACKEND", "chroma")
        monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "test-key")
        monkeypatch.setenv("CHROMA_PERSIST_DIR", str(tmp_path / "chroma"))
        get_settings.cache_clear()

        response = client.get("/health/ready")
        assert response.status_code == 200
        data = response.json()
        assert data["ready"] is True
        assert data["status"] == "ready"
        component_names = {c["name"] for c in data["components"]}
        assert {"database", "queue", "vector_db", "llm", "storage"} <= component_names

    def test_readiness_503_when_core_down(self, client, monkeypatch, tmp_path):
        """核心组件（LLM 凭证缺失）down → 503 + ready=false。"""
        from app.config.settings import get_settings

        monkeypatch.setenv("TASK_QUEUE_BACKEND", "memory")
        monkeypatch.setenv("TASK_STORAGE_BACKEND", "memory")
        monkeypatch.setenv("CHECKPOINT_BACKEND", "memory")
        monkeypatch.setenv("VECTOR_STORE_BACKEND", "chroma")
        monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "")
        monkeypatch.setenv("CHROMA_PERSIST_DIR", str(tmp_path / "chroma"))
        get_settings.cache_clear()

        response = client.get("/health/ready")
        assert response.status_code == 503
        data = response.json()
        assert data["ready"] is False
        assert data["status"] == "not_ready"

    def test_task_creation_blocked_when_not_ready_in_production(
        self, client, monkeypatch, tmp_path
    ):
        """生产模式未就绪：POST /tasks 返回 503（拒绝接收任务）。"""
        import app.api.deps as deps
        from app.config.settings import get_settings

        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("TASK_QUEUE_BACKEND", "redis")
        monkeypatch.setenv("TASK_QUEUE_EMBEDDED_WORKER", "false")
        monkeypatch.setenv("TASK_STORAGE_BACKEND", "postgres")
        monkeypatch.setenv("CHECKPOINT_BACKEND", "postgres")
        monkeypatch.setenv("VECTOR_STORE_BACKEND", "chroma")
        monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "test-key")
        monkeypatch.setenv("CHROMA_PERSIST_DIR", str(tmp_path / "chroma"))
        monkeypatch.setenv("REDIS_PORT", str(UNREACHABLE_PORT))
        get_settings.cache_clear()

        # 重置进程级单例，避免其他测试残留的开发模式实例
        monkeypatch.setattr(deps, "_task_service", None)
        monkeypatch.setattr(deps, "_task_queue", None)

        response = client.post(
            "/api/v1/tasks/", json={"goal": "生产模式就绪门禁验证"}
        )
        assert response.status_code == 503
        assert "未就绪" in response.json()["detail"]

    def test_task_creation_allowed_in_development(self, client, monkeypatch):
        """开发模式门禁直通：任务创建不受就绪状态影响。"""
        from app.api.deps import get_task_service
        from app.config.settings import get_settings
        from app.services.task_service import TaskService
        from main import app

        monkeypatch.setenv("ENVIRONMENT", "development")
        get_settings.cache_clear()
        svc = TaskService(Settings(TASK_STORAGE_BACKEND="memory"))
        app.dependency_overrides[get_task_service] = lambda: svc
        try:
            response = client.post(
                "/api/v1/tasks/", json={"goal": "开发模式门禁直通验证"}
            )
            assert response.status_code == 201
        finally:
            app.dependency_overrides.pop(get_task_service, None)
