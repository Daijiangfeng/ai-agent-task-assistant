"""
LangGraph Checkpoint 工厂。

真正启用执行状态持久化：
- 每个任务以 thread_id=task_id 命名检查点，节点执行进度落库，
  服务崩溃/中断后可基于同一 thread_id 断点续跑；
- 后端可插拔（CHECKPOINT_BACKEND）：
    - auto（默认）：优先 PostgreSQL（AsyncPostgresSaver），不可用降级内存；
    - postgres：强制 PostgreSQL，连接失败抛错（配置错误应显式暴露）；
    - memory：进程内 MemorySaver（开发/测试）。
"""

from __future__ import annotations

from typing import Any

from app.config.logging import get_logger
from app.config.settings import Settings, get_settings

logger = get_logger(__name__)

# 已创建的 PostgreSQL 连接池（进程级注册），供应用/测试在事件循环结束前统一关闭，
# 避免 Windows 上 asyncio 退出时池内进行中的连接任务被取消后无限重排导致进程挂起。
_OPEN_POOLS: list[Any] = []


def register_pool(pool: Any) -> None:
    """注册连接池以便生命周期统一关闭（幂等）。"""
    if pool not in _OPEN_POOLS:
        _OPEN_POOLS.append(pool)


async def close_all_checkpoint_pools() -> None:
    """关闭所有已注册的 checkpoint 连接池（应用关闭/测试收尾时调用）。"""
    while _OPEN_POOLS:
        pool = _OPEN_POOLS.pop()
        try:
            await pool.close()
        except Exception as exc:  # noqa: BLE001 - 关闭失败不应阻断收尾
            logger.warning("Checkpoint 连接池关闭失败", error=str(exc))


def create_memory_saver() -> Any:
    """创建进程内 MemorySaver（开发/测试/降级用）。"""
    from langgraph.checkpoint.memory import InMemorySaver

    return InMemorySaver()


async def _create_postgres_saver(
    settings: Settings, required: bool
) -> Any:
    """
    创建 AsyncPostgresSaver（psycopg 异步连接池）。

    Args:
        settings: 配置对象。
        required: True 时失败直接抛出；False（auto）时失败返回 None 由调用方降级。

    Returns:
        AsyncPostgresSaver 实例；非 required 模式下失败返回 None。
    """
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from psycopg_pool import AsyncConnectionPool

    class _SetupAutocommitPostgresSaver(AsyncPostgresSaver):
        """
        AsyncPostgresSaver 子类：setup 迁移在 autocommit 会话中执行。

        langgraph-checkpoint-postgres 的 setup() 含 `CREATE INDEX CONCURRENTLY`，
        该语句不允许在事务块内运行（psycopg 默认非 autocommit 会直接失败）。
        此处以 autocommit 连接重放全部幂等迁移（IF NOT EXISTS 可安全重复），
        结束后恢复非 autocommit，保证连接归还连接池时状态干净。
        """

        async def setup(self) -> None:
            from langgraph.checkpoint.postgres import _ainternal

            async with self.lock, _ainternal.get_connection(self.conn) as conn:
                await conn.set_autocommit(True)
                try:
                    async with conn.cursor() as cur:
                        for migration in self.MIGRATIONS:
                            await cur.execute(migration)
                finally:
                    await conn.set_autocommit(False)

    pool = AsyncConnectionPool(
        conninfo=settings.postgres_dsn,
        open=False,
        kwargs={"connect_timeout": settings.DB_CONNECT_TIMEOUT},
    )
    try:
        await pool.open()
        register_pool(pool)
        saver = _SetupAutocommitPostgresSaver(pool)
        await saver.setup()
        return saver
    except Exception as exc:
        await pool.close()
        if required:
            raise RuntimeError(
                f"PostgreSQL Checkpoint 初始化失败（CHECKPOINT_BACKEND=postgres）：{exc}"
            ) from exc
        logger.warning("PostgreSQL Checkpoint 不可用，将降级", error=str(exc))
        return None


async def create_checkpointer(settings: Settings | None = None) -> Any | None:
    """
    按配置创建 LangGraph checkpointer。

    Args:
        settings: 配置对象，默认使用全局配置。

    Returns:
        checkpointer 实例；ENABLE_CHECKPOINTING=false 时返回 None（不启用检查点）。
    """
    settings = settings or get_settings()
    if not settings.ENABLE_CHECKPOINTING:
        logger.info("Checkpoint 未启用（ENABLE_CHECKPOINTING=false）")
        return None

    backend = settings.CHECKPOINT_BACKEND.strip().lower()
    if settings.is_production and backend == "memory":
        # 生产禁止 MemorySaver：多 Worker 不共享、重启丢断点（静默降级）
        raise RuntimeError(
            "生产环境禁止 MemorySaver 检查点（CHECKPOINT_BACKEND=memory），"
            "请配置 CHECKPOINT_BACKEND=postgres"
        )
    if backend == "memory":
        return create_memory_saver()
    if backend == "postgres":
        return await _create_postgres_saver(settings, required=True)
    if settings.is_production:
        # 生产：auto 等价 postgres，禁止降级
        return await _create_postgres_saver(settings, required=True)

    # auto：优先 PostgreSQL，失败降级 MemorySaver
    saver = await _create_postgres_saver(settings, required=False)
    if saver is not None:
        return saver
    logger.warning("Checkpoint 降级为进程内 MemorySaver（重启后检查点丢失）")
    return create_memory_saver()
