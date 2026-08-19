"""
进程内内存任务队列（单进程开发/测试用）。

基于 asyncio.Queue 实现：无外部依赖、无需 Redis，API 入队后由应用内嵌
Worker 在同一事件循环消费。进程重启后积压消息丢失（与 Redis 队列的
可靠性差异见 app/queue/redis_queue.py 说明）。
"""

from __future__ import annotations

import asyncio

from app.queue.base import QueueFullError, TaskMessage, TaskQueue


class InMemoryTaskQueue(TaskQueue):
    """内存任务队列（FIFO）。"""

    def __init__(self, maxsize: int = 1000):
        self._queue: asyncio.Queue[TaskMessage] = asyncio.Queue(maxsize=maxsize)

    async def enqueue(self, message: TaskMessage) -> None:
        try:
            self._queue.put_nowait(message)
        except asyncio.QueueFull as e:
            raise QueueFullError(f"任务队列已满（容量 {self._queue.maxsize}）") from e

    async def dequeue(self, timeout: float = 1.0) -> TaskMessage | None:
        try:
            return await asyncio.wait_for(self._queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None

    async def close(self) -> None:
        pass

    async def size(self) -> int:
        return self._queue.qsize()
