"""
记忆系统抽象基类。
定义短期记忆和长期记忆的统一接口。
"""

from abc import ABC, abstractmethod
from typing import Any, Optional


class BaseMemory(ABC):
    """
    记忆系统抽象基类。

    短期记忆实现：Redis（保存会话上下文和任务状态）
    长期记忆实现：Vector Database（保存用户偏好和历史任务）
    """

    @abstractmethod
    async def save(self, key: str, value: Any, ttl: int | None = None) -> None:
        """
        保存记忆。

        Args:
            key: 记忆键。
            value: 记忆值。
            ttl: 过期时间（秒），None 表示永不过期。
        """
        ...

    @abstractmethod
    async def get(self, key: str) -> Optional[Any]:
        """
        获取记忆。

        Args:
            key: 记忆键。

        Returns:
            记忆值，不存在返回 None。
        """
        ...

    @abstractmethod
    async def delete(self, key: str) -> None:
        """
        删除记忆。

        Args:
            key: 记忆键。
        """
        ...

    @abstractmethod
    async def search(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        """
        语义搜索记忆。

        Args:
            query: 搜索查询文本。
            top_k: 返回最相关的 top_k 条结果。

        Returns:
            记忆列表，每项包含 key、value、score 等。
        """
        ...
