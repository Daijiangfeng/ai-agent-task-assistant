"""
短期记忆实现。
基于 Redis 保存会话上下文和任务状态，连接失败时自动降级为进程内内存。
"""

from __future__ import annotations

import json
import time
from typing import Any, Optional

from app.config.logging import get_logger
from app.config.settings import Settings, get_settings
from app.memory.base import BaseMemory

logger = get_logger(__name__)


class InMemoryShortTermMemory(BaseMemory):
    """
    进程内短期记忆。

    使用字典 + 时间戳实现 TTL，作为 Redis 不可用时的降级方案，
    也可用于测试。注意：进程重启后数据丢失。
    """

    def __init__(self):
        # key -> (value, expire_at | None)
        self._store: dict[str, tuple[Any, float | None]] = {}

    def _is_expired(self, key: str) -> bool:
        item = self._store.get(key)
        if item is None:
            return True
        _, expire_at = item
        if expire_at is not None and time.time() > expire_at:
            self._store.pop(key, None)
            return True
        return False

    async def save(self, key: str, value: Any, ttl: int | None = None) -> None:
        expire_at = time.time() + ttl if ttl else None
        self._store[key] = (value, expire_at)

    async def get(self, key: str) -> Optional[Any]:
        if self._is_expired(key):
            return None
        return self._store[key][0]

    async def delete(self, key: str) -> None:
        self._store.pop(key, None)

    async def search(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        """短期记忆不支持语义搜索，按 key 前缀匹配返回。"""
        results: list[dict[str, Any]] = []
        for key in list(self._store.keys()):
            if self._is_expired(key):
                continue
            if query in key:
                results.append({"key": key, "value": self._store[key][0], "score": 1.0})
            if len(results) >= top_k:
                break
        return results


class RedisShortTermMemory(BaseMemory):
    """
    基于 Redis 的短期记忆。

    value 以 JSON 序列化存储，支持 TTL。
    初始化时尝试连接 Redis，失败则降级到 InMemoryShortTermMemory。
    """

    def __init__(self, settings: Settings | None = None):
        self._settings = settings or get_settings()
        self._fallback = InMemoryShortTermMemory()
        self._redis = None
        self._degraded = False
        self._prefix = "stm:"

        try:
            import redis.asyncio as aioredis

            self._redis = aioredis.from_url(
                self._settings.redis_url,
                encoding="utf-8",
                decode_responses=True,
            )
        except Exception as exc:  # pragma: no cover - 依赖缺失或配置错误
            logger.warning("Redis 初始化失败，降级为内存短期记忆", error=str(exc))
            self._degraded = True

    async def _ensure_connection(self) -> bool:
        """确认 Redis 可用，不可用则标记降级。"""
        if self._degraded or self._redis is None:
            return False
        try:
            await self._redis.ping()
            return True
        except Exception as exc:
            logger.warning("Redis 连接不可用，降级为内存", error=str(exc))
            self._degraded = True
            return False

    def _k(self, key: str) -> str:
        return f"{self._prefix}{key}"

    async def save(self, key: str, value: Any, ttl: int | None = None) -> None:
        if not await self._ensure_connection():
            return await self._fallback.save(key, value, ttl)
        payload = json.dumps(value, ensure_ascii=False, default=str)
        await self._redis.set(self._k(key), payload, ex=ttl)

    async def get(self, key: str) -> Optional[Any]:
        if not await self._ensure_connection():
            return await self._fallback.get(key)
        raw = await self._redis.get(self._k(key))
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw

    async def delete(self, key: str) -> None:
        if not await self._ensure_connection():
            return await self._fallback.delete(key)
        await self._redis.delete(self._k(key))

    async def search(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        """按 key 模式扫描匹配（短期记忆不做语义检索）。"""
        if not await self._ensure_connection():
            return await self._fallback.search(query, top_k)

        results: list[dict[str, Any]] = []
        pattern = f"{self._prefix}*{query}*"
        async for redis_key in self._redis.scan_iter(match=pattern, count=100):
            raw = await self._redis.get(redis_key)
            value = None
            if raw is not None:
                try:
                    value = json.loads(raw)
                except json.JSONDecodeError:
                    value = raw
            results.append(
                {
                    "key": redis_key.replace(self._prefix, ""),
                    "value": value,
                    "score": 1.0,
                }
            )
            if len(results) >= top_k:
                break
        return results
