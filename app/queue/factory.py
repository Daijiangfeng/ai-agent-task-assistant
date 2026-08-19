"""
任务队列工厂。
按 TASK_QUEUE_BACKEND 选择后端：auto 优先 Redis（探测失败降级内存，
生产环境禁止降级，探测失败直接抛错）。
"""

from __future__ import annotations

import asyncio
import concurrent.futures

from app.config.logging import get_logger
from app.config.settings import Settings, get_settings
from app.queue.base import TaskQueue
from app.queue.memory_queue import InMemoryTaskQueue
from app.queue.redis_queue import RedisTaskQueue

logger = get_logger(__name__)


def _probe_redis(queue: RedisTaskQueue) -> bool:
    """探测 Redis 可达性（同步入口，兼容已在事件循环内的调用场景）。

    - 无运行中的事件循环：直接 asyncio.run 探测；
    - 已在事件循环内（如 FastAPI lifespan 中初始化内嵌 Worker）：
      asyncio.run 会因嵌套报错，此处放入独立线程的事件循环探测，
      修复原先「事件循环内 auto 探测必然失败 → 静默降级内存队列」的缺陷。
    """

    async def _probe() -> bool:
        ok = await queue.ping()
        await queue.close()
        return ok

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        try:
            return asyncio.run(_probe())
        except Exception:
            return False

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        try:
            return executor.submit(lambda: asyncio.run(_probe())).result()
        except Exception:
            return False


def create_task_queue(settings: Settings | None = None) -> TaskQueue:
    """
    创建任务队列实例。

    backend:
        - auto: 尝试 Redis 连接（短超时探测），失败降级内存队列；
          生产环境（ENVIRONMENT=production）禁止降级，探测失败抛错；
        - redis: 强制 Redis（不可用时直接抛错，便于尽早暴露配置问题）；
        - memory: 强制内存队列（单进程开发/测试；生产环境禁止）。

    Returns:
        TaskQueue 实例。

    Raises:
        RuntimeError: 生产环境配置了内存队列，或 auto 模式 Redis 不可达。
    """
    settings = settings or get_settings()
    backend = settings.TASK_QUEUE_BACKEND

    if backend == "memory":
        if settings.is_production:
            raise RuntimeError(
                "生产环境禁止内存任务队列（TASK_QUEUE_BACKEND=memory），"
                "请配置 TASK_QUEUE_BACKEND=redis"
            )
        logger.info("任务队列: 使用内存后端")
        return InMemoryTaskQueue()

    if backend == "redis":
        logger.info("任务队列: 使用 Redis 后端")
        return RedisTaskQueue(settings)

    # auto：Redis 可用则用 Redis，否则降级内存（开发机未启动 Redis 也开箱可用）
    queue = RedisTaskQueue(settings)
    if not _probe_redis(queue):
        if settings.is_production:
            raise RuntimeError(
                "生产环境 Redis 任务队列不可达"
                "（TASK_QUEUE_BACKEND=auto 禁止降级内存），"
                "请检查 Redis 连接配置"
            )
        logger.warning("任务队列: Redis 不可达，降级为内存后端")
        return InMemoryTaskQueue()
    logger.info("任务队列: 使用 Redis 后端（auto）")
    return queue
