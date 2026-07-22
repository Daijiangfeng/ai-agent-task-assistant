"""
记忆系统工厂。
提供短期记忆和长期记忆的统一创建入口，供服务层与 API 注入。
"""

from __future__ import annotations

from app.config.logging import get_logger
from app.config.settings import Settings, get_settings
from app.memory.base import BaseMemory
from app.memory.long_term import VectorLongTermMemory
from app.memory.short_term import InMemoryShortTermMemory, RedisShortTermMemory

logger = get_logger(__name__)


class MemoryFactory:
    """
    记忆系统工厂。

    统一创建短期记忆（Redis + 内存降级）和长期记忆（Chroma 向量库）。
    """

    @staticmethod
    def create_short_term(
        settings: Settings | None = None,
        use_redis: bool = True,
    ) -> BaseMemory:
        """
        创建短期记忆实例。

        Args:
            settings: 配置对象，默认使用全局配置。
            use_redis: 是否尝试使用 Redis，False 则直接返回内存实现。

        Returns:
            BaseMemory 实例（RedisShortTermMemory 或 InMemoryShortTermMemory）。
        """
        settings = settings or get_settings()
        if not use_redis:
            return InMemoryShortTermMemory()
        return RedisShortTermMemory(settings)

    @staticmethod
    def create_long_term(
        settings: Settings | None = None,
    ) -> VectorLongTermMemory:
        """
        创建长期记忆实例（基于 Chroma 向量库）。

        Args:
            settings: 配置对象，默认使用全局配置。

        Returns:
            VectorLongTermMemory 实例。
        """
        settings = settings or get_settings()
        return VectorLongTermMemory(settings)
