"""
基础设施健康检查服务（P0-1 生产安全模式）。

- InfrastructureHealthChecker：并行探测 PostgreSQL / 任务队列 / 向量库 /
  LLM Provider / 存储 / Checkpoint / Redis 等组件可用性；
- ReadinessGate：带 TTL 缓存的就绪状态门禁，供
  - ``GET /health/ready`` 就绪探针、
  - ``require_ready`` 任务接收门禁（生产模式 503 拒绝）、
  - 应用启动 fail-fast 校验（verify_production_readiness）
  复用，避免每次请求重复探测。

就绪语义：ready = 无任何 core 组件 down。
core=False 的组件（如开发模式 auto 降级、信息性 Redis 探测）故障不阻断就绪，
但在 /health/ready 响应中如实展示。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config.logging import get_logger
from app.config.settings import Settings, get_settings

logger = get_logger(__name__)

STATUS_UP = "up"
STATUS_DOWN = "down"
STATUS_SKIPPED = "skipped"


@dataclass
class ComponentStatus:
    """单组件健康状态。"""

    name: str
    status: str  # up | down | skipped
    detail: str = ""
    latency_ms: float = 0.0
    # core=True 的组件 down 时整体 ready=False（生产阻断、开发仅如实展示）
    core: bool = True


@dataclass
class HealthReport:
    """整体健康报告。"""

    ready: bool
    environment: str
    checked_at: str
    components: list[ComponentStatus] = field(default_factory=list)

    def failed_core_components(self) -> list[str]:
        """返回 down 状态的核心组件名。"""
        return [c.name for c in self.components if c.status == STATUS_DOWN and c.core]

    def to_dict(self) -> dict[str, Any]:
        """序列化为 API 响应友好结构。"""
        return {
            "ready": self.ready,
            "environment": self.environment,
            "checked_at": self.checked_at,
            "components": [
                {
                    "name": c.name,
                    "status": c.status,
                    "detail": c.detail,
                    "latency_ms": round(c.latency_ms, 1),
                    "core": c.core,
                }
                for c in self.components
            ],
        }


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _ping_postgres(settings: Settings) -> tuple[bool, str]:
    """探测 PostgreSQL 连通性（asyncpg 直连，短超时）。"""
    import asyncpg

    dsn = settings.postgres_async_dsn.replace(
        "postgresql+asyncpg://", "postgresql://", 1
    )
    conn = await asyncio.wait_for(
        asyncpg.connect(dsn=dsn, timeout=settings.HEALTH_CHECK_TIMEOUT),
        timeout=settings.HEALTH_CHECK_TIMEOUT + 1.0,
    )
    try:
        await conn.fetchval("SELECT 1")
        return True, "PostgreSQL 连接正常"
    finally:
        await conn.close()


async def _ping_redis(settings: Settings) -> tuple[bool, str]:
    """探测 Redis 连通性（async redis，短超时）。"""
    import redis.asyncio as aioredis

    client = aioredis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=settings.HEALTH_CHECK_TIMEOUT,
    )
    try:
        await asyncio.wait_for(
            client.ping(), timeout=settings.HEALTH_CHECK_TIMEOUT + 1.0
        )
        return True, "Redis 连接正常"
    finally:
        await client.aclose()


class InfrastructureHealthChecker:
    """基础设施健康检查器（组件并行探测，单个组件超时不拖累整体）。"""

    def __init__(self, settings: Settings | None = None):
        self._settings = settings or get_settings()

    async def check(self) -> HealthReport:
        """执行全部组件探测并汇总就绪结论。"""
        settings = self._settings
        # (组件名, 是否核心, 探测函数)：兜底路径（探测挂起/意外异常）依此构造
        specs: list[tuple[str, bool, Any]] = [
            ("database", True, self._check_database),
            ("queue", True, self._check_queue),
            ("vector_db", True, self._check_vector_db),
            ("llm", True, self._check_llm),
            ("storage", True, self._check_storage),
            ("checkpoint", True, self._check_checkpoint),
            ("redis", False, self._check_redis),
        ]
        components = await asyncio.gather(
            *(self._run_check(name, core, fn) for name, core, fn in specs)
        )
        ready = not any(
            c.status == STATUS_DOWN and c.core for c in components
        )
        report = HealthReport(
            ready=ready,
            environment=settings.ENVIRONMENT,
            checked_at=_utcnow_iso(),
            components=list(components),
        )
        if not ready:
            logger.warning(
                "基础设施健康检查未通过",
                failed=report.failed_core_components(),
            )
        return report

    async def _run_check(self, name: str, core: bool, fn) -> ComponentStatus:
        """执行单个组件探测，统一超时与异常兜底（保留组件 core 语义）。

        内层各探测函数自带精细超时（且正确区分开发降级 core=False），
        此处外层超时仅作防挂起兜底，因此留出连接建立与 chroma 首次
        导入等余量。
        """
        started = time.perf_counter()
        outer_timeout = self._settings.HEALTH_CHECK_TIMEOUT + 10.0
        try:
            component = await asyncio.wait_for(fn(), timeout=outer_timeout)
        except asyncio.TimeoutError:
            component = ComponentStatus(
                name=name,
                status=STATUS_DOWN,
                detail=f"探测超时（>{outer_timeout:.0f}s）",
                core=core,
            )
        except Exception as exc:  # noqa: BLE001 - 探测异常一律记为组件 down
            component = ComponentStatus(
                name=name,
                status=STATUS_DOWN,
                detail=f"探测异常: {exc}",
                core=core,
            )
        component.latency_ms = (time.perf_counter() - started) * 1000
        return component

    # ------------------------------------------------------------------
    # 组件探测
    # ------------------------------------------------------------------

    async def _check_database(self) -> ComponentStatus:
        """任务存储数据库（postgres / sqlite / memory）。"""
        settings = self._settings
        backend = settings.TASK_STORAGE_BACKEND.lower()
        if backend in ("postgres", "auto"):
            try:
                ok, detail = await _ping_postgres(settings)
            except Exception as exc:
                ok, detail = False, f"PostgreSQL 不可达: {exc}"
            if ok:
                return ComponentStatus("database", STATUS_UP, detail)
            if settings.is_production or backend == "postgres":
                return ComponentStatus("database", STATUS_DOWN, detail)
            # 开发模式 auto：设计内降级路径，故障不阻断就绪
            return ComponentStatus(
                "database",
                STATUS_DOWN,
                f"{detail}（开发模式 auto 将降级内存存储）",
                core=False,
            )
        if backend == "sqlite":
            try:
                path = Path(settings.task_db_path)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch(exist_ok=True)
                return ComponentStatus("database", STATUS_UP, f"SQLite 任务库可用（{path}）")
            except Exception as exc:
                return ComponentStatus("database", STATUS_DOWN, f"SQLite 任务库不可写: {exc}")
        # memory：生产环境已被工厂守卫拒绝，此处仅兜底展示
        if settings.is_production:
            return ComponentStatus(
                "database", STATUS_DOWN, "生产环境禁止内存任务存储"
            )
        return ComponentStatus(
            "database", STATUS_UP, "内存任务存储（仅开发）", core=False
        )

    async def _check_queue(self) -> ComponentStatus:
        """任务队列（redis / memory / auto）。"""
        settings = self._settings
        backend = settings.TASK_QUEUE_BACKEND.lower()
        if backend == "memory":
            if settings.is_production:
                return ComponentStatus(
                    "queue", STATUS_DOWN, "生产环境禁止内存任务队列"
                )
            return ComponentStatus(
                "queue", STATUS_UP, "内存队列（仅开发/单进程）", core=False
            )
        try:
            ok, detail = await _ping_redis(settings)
        except Exception as exc:
            ok, detail = False, f"Redis 不可达: {exc}"
        if ok:
            return ComponentStatus("queue", STATUS_UP, f"{detail}（backend={backend}）")
        if settings.is_production or backend == "redis":
            return ComponentStatus("queue", STATUS_DOWN, detail)
        return ComponentStatus(
            "queue",
            STATUS_DOWN,
            f"{detail}（开发模式 auto 将降级内存队列）",
            core=False,
        )

    async def _check_vector_db(self) -> ComponentStatus:
        """向量库（chroma 本地持久化 / pgvector）。"""
        settings = self._settings
        backend = settings.VECTOR_STORE_BACKEND.lower()
        if backend == "pgvector":
            try:
                ok, detail = await _ping_postgres(settings)
            except Exception as exc:
                ok, detail = False, f"pgvector 所在 PostgreSQL 不可达: {exc}"
            status = STATUS_UP if ok else STATUS_DOWN
            return ComponentStatus("vector_db", status, f"{detail}（pgvector）")
        # chroma：本地持久化目录可初始化即视为可用
        try:
            import chromadb

            client = chromadb.PersistentClient(path=settings.chroma_dir)
            client.heartbeat()
            return ComponentStatus(
                "vector_db", STATUS_UP, f"Chroma 可用（{settings.chroma_dir}）"
            )
        except Exception as exc:
            return ComponentStatus("vector_db", STATUS_DOWN, f"Chroma 初始化失败: {exc}")

    async def _check_llm(self) -> ComponentStatus:
        """LLM Provider：凭证已配置且可实例化（不做真实调用，避免探测成本）。"""
        settings = self._settings
        token = (settings.ANTHROPIC_AUTH_TOKEN or "").strip()
        if not token:
            return ComponentStatus(
                "llm",
                STATUS_DOWN,
                "ANTHROPIC_AUTH_TOKEN 未配置，Agent 任务无法执行",
            )
        return ComponentStatus(
            "llm", STATUS_UP, f"智谱 GLM 已配置（模型 {settings.ZHIPU_MODEL}）"
        )

    async def _check_storage(self) -> ComponentStatus:
        """本地存储：数据目录可写（Chroma 目录 / SQLite 沙箱 / 任务库目录）。"""
        settings = self._settings
        candidates = [
            Path(settings.chroma_dir),
            Path(settings.sqlite_sandbox_path).parent,
        ]
        if settings.TASK_STORAGE_BACKEND.lower() == "sqlite":
            candidates.append(Path(settings.task_db_path).parent)
        try:
            for directory in candidates:
                directory.mkdir(parents=True, exist_ok=True)
                probe = directory / ".health_probe"
                probe.write_text("ok", encoding="utf-8")
                probe.unlink()
            return ComponentStatus(
                "storage", STATUS_UP, f"数据目录可写（{candidates[0]} 等）"
            )
        except Exception as exc:
            return ComponentStatus("storage", STATUS_DOWN, f"数据目录不可写: {exc}")

    async def _check_checkpoint(self) -> ComponentStatus:
        """LangGraph Checkpoint 后端（postgres / memory / auto）。"""
        settings = self._settings
        if not settings.ENABLE_CHECKPOINTING:
            return ComponentStatus(
                "checkpoint", STATUS_SKIPPED, "ENABLE_CHECKPOINTING=false", core=False
            )
        backend = settings.CHECKPOINT_BACKEND.strip().lower()
        if backend == "memory":
            if settings.is_production:
                return ComponentStatus(
                    "checkpoint", STATUS_DOWN, "生产环境禁止 MemorySaver"
                )
            return ComponentStatus(
                "checkpoint", STATUS_UP, "MemorySaver（仅开发）", core=False
            )
        try:
            ok, detail = await _ping_postgres(settings)
        except Exception as exc:
            ok, detail = False, f"PostgreSQL 不可达: {exc}"
        if ok:
            return ComponentStatus("checkpoint", STATUS_UP, f"{detail}（backend={backend}）")
        if settings.is_production or backend == "postgres":
            return ComponentStatus("checkpoint", STATUS_DOWN, detail)
        return ComponentStatus(
            "checkpoint",
            STATUS_DOWN,
            f"{detail}（开发模式 auto 将降级 MemorySaver）",
            core=False,
        )

    async def _check_redis(self) -> ComponentStatus:
        """Redis 连通性（信息性：队列/短期记忆共用；就绪判定以 queue 组件为准）。"""
        try:
            ok, detail = await _ping_redis(self._settings)
        except Exception as exc:
            ok, detail = False, f"Redis 不可达: {exc}"
        status = STATUS_UP if ok else STATUS_DOWN
        return ComponentStatus("redis", status, f"{detail}（信息性，供队列/STM 共用）", core=False)


class ReadinessGate:
    """就绪状态门禁（TTL 缓存，避免高频探测）。"""

    def __init__(
        self,
        checker: InfrastructureHealthChecker | None = None,
        ttl_seconds: float | None = None,
    ):
        self._checker = checker or InfrastructureHealthChecker()
        settings = get_settings()
        self._ttl = settings.HEALTH_CACHE_TTL if ttl_seconds is None else ttl_seconds
        self._lock = asyncio.Lock()
        self._report: HealthReport | None = None
        self._checked_at: float = 0.0

    async def get_report(self, force: bool = False) -> HealthReport:
        """获取就绪报告（缓存新鲜时直接复用；force=True 强制重探）。"""
        async with self._lock:
            fresh = (
                self._report is not None
                and self._ttl > 0
                and (time.monotonic() - self._checked_at) <= self._ttl
            )
            if not force and fresh:
                return self._report  # type: ignore[return-value]
            self._report = await self._checker.check()
            self._checked_at = time.monotonic()
            return self._report


# ---- 模块级单例（依赖注入 + 测试隔离） ----

_health_checker: InfrastructureHealthChecker | None = None
_readiness_gate: ReadinessGate | None = None


def get_health_checker() -> InfrastructureHealthChecker:
    """获取健康检查器单例。"""
    global _health_checker
    if _health_checker is None:
        _health_checker = InfrastructureHealthChecker()
    return _health_checker


def get_readiness_gate() -> ReadinessGate:
    """获取就绪门禁单例。"""
    global _readiness_gate
    if _readiness_gate is None:
        _readiness_gate = ReadinessGate(get_health_checker())
    return _readiness_gate


def reset_readiness_gate() -> None:
    """重置就绪门禁与健康检查器单例（测试隔离用）。

    注意：checker 在构造时快照 Settings，隔离时必须与 gate 一并重置，
    否则会复用旧配置的检查器（settings 单测中的经典陷阱）。
    """
    global _readiness_gate, _health_checker
    _readiness_gate = None
    _health_checker = None


async def verify_production_readiness(settings: Settings | None = None) -> HealthReport:
    """生产环境启动校验：核心基础设施不可用直接抛错（fail-fast）。"""
    settings = settings or get_settings()
    checker = InfrastructureHealthChecker(settings)
    report = await checker.check()
    if not report.ready:
        failed = ", ".join(report.failed_core_components())
        raise RuntimeError(
            f"生产环境（ENVIRONMENT=production）基础设施健康检查未通过，"
            f"启动终止。失败组件: {failed}。"
            "请检查 PostgreSQL / Redis / 向量库 / LLM 凭证 / 存储目录配置。"
        )
    return report
