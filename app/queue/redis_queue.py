"""
Redis 任务队列（生产环境推荐）。

基于 Redis List 实现可靠 FIFO 队列：
- enqueue: LPUSH（写入内存 + 落盘持久化，进程崩溃不丢消息）；
- dequeue: BRPOP（阻塞弹出，Worker 空闲时不轮询空转）；
- 支持多 Worker 并发消费（同一 key，天然负载均衡）；
- 消息以 JSON 序列化存储，跨进程/跨语言可见（可运维、可监控积压）。

注意：本队列不实现"消费确认/重投"（与 Celery 等系统不同）——
Agent 任务以 LangGraph Checkpoint（thread_id=task_id）保证崩溃后可
断点续跑，出队即执行、执行幂等可恢复，故无需 ACK 语义。
"""

from __future__ import annotations

import json

from redis.asyncio import Redis

from app.config.logging import get_logger
from app.config.settings import Settings
from app.queue.base import TaskMessage, TaskQueue

logger = get_logger(__name__)


class RedisTaskQueue(TaskQueue):
    """Redis List 任务队列。"""

    # 连接建立超时（秒）：避免网络不可达时长时间阻塞（不影响已建连的 BRPOP 阻塞读）
    CONNECT_TIMEOUT = 2.0

    def __init__(self, settings: Settings):
        self._settings = settings
        self._client: Redis = Redis(
            host=settings.REDIS_HOST,
            port=settings.REDIS_PORT,
            db=settings.REDIS_DB,
            decode_responses=True,
            socket_connect_timeout=self.CONNECT_TIMEOUT,
        )
        self._key = settings.REDIS_QUEUE_KEY

    async def ping(self) -> bool:
        """探测 Redis 是否可达（factory auto 模式用）。"""
        try:
            return bool(await self._client.ping())
        except Exception:
            return False

    async def enqueue(self, message: TaskMessage) -> None:
        # LPUSH 单次写入；若需限流可在此前追加 LLEN 检查
        await self._client.lpush(self._key, json.dumps(message.to_dict()))

    async def dequeue(self, timeout: float = 1.0) -> TaskMessage | None:
        try:
            _key, payload = await self._client.brpop(self._key, timeout=timeout)
        except TypeError:
            return None  # 超时返回 (None, None)
        if payload is None:
            return None
        try:
            return TaskMessage.from_dict(json.loads(payload))
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning("Redis 队列消息解析失败，丢弃", error=str(e), payload=payload)
            return None

    async def close(self) -> None:
        try:
            await self._client.aclose()
        except Exception:  # pragma: no cover - 关闭路径容错
            pass

    async def size(self) -> int:
        return await self._client.llen(self._key)
