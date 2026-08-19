"""
搜索结果进程内 TTL 缓存。

- 普通知识问题可复用较长时间（WEB_SEARCH_CACHE_TTL），降低相同 query 的重复成本。
- 时效性/新闻问题使用短 TTL（WEB_SEARCH_TIME_SENSITIVE_CACHE_TTL）。
- '今天/刚刚/当前/最近版本'等强时效问题在调用方绕过缓存（max_age=0）。
"""

from __future__ import annotations

import threading
import time


class SearchResultCache:
    """带时间戳的进程内缓存，按 (max_age) 决定是否命中，避免长期复用旧结果。"""

    def __init__(self) -> None:
        self._store: dict[str, tuple[object, float]] = {}
        self._lock = threading.Lock()

    def get(self, key: str, max_age: float = 0) -> object | None:
        """返回未过期的缓存结果；max_age<=0 表示绕过缓存。"""
        if max_age <= 0:
            return None
        now = time.monotonic()
        with self._lock:
            item = self._store.get(key)
            if item is None:
                return None
            value, ts = item
            if now - ts < max_age:
                return value
            self._store.pop(key, None)
            return None

    def set(self, key: str, value: object) -> None:
        with self._lock:
            self._store[key] = (value, time.monotonic())

    def clear(self) -> None:
        with self._lock:
            self._store.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._store)


_default_cache = SearchResultCache()


def get_search_cache() -> SearchResultCache:
    return _default_cache
