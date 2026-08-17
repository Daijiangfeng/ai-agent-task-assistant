"""
记忆系统抽象基类。
定义短期记忆和长期记忆的统一接口。

多租户/多用户隔离：所有读写操作必须携带 user_id 与 tenant_id 作用域。
长期记忆（向量库）实现会在元数据中写入作用域并强制按作用域过滤检索，
避免用户 A 的记忆被用户 B 召回。
"""

from abc import ABC, abstractmethod
from typing import Any, Optional

DEFAULT_USER_ID = "anonymous"
DEFAULT_TENANT_ID = "default"


class BaseMemory(ABC):
    """
    记忆系统抽象基类。

    短期记忆实现：Redis（保存会话上下文和任务状态）
    长期记忆实现：Vector Database（保存用户偏好和历史任务）

    隔离约定：
    - save/get/delete/search 均需携带 user_id / tenant_id 作用域；
    - 短期记忆实现由调用方保证 key 已命名空间化（如 session:{tenant}:{user}:{id}），
      长期记忆实现强制校验作用域。
    """

    @abstractmethod
    async def save(
        self,
        key: str,
        value: Any,
        ttl: int | None = None,
        *,
        user_id: str = DEFAULT_USER_ID,
        tenant_id: str = DEFAULT_TENANT_ID,
    ) -> None:
        """
        保存记忆。

        Args:
            key: 记忆键。
            value: 记忆值。
            ttl: 过期时间（秒），None 表示永不过期。
            user_id: 所有者用户 ID（数据隔离维度之一）。
            tenant_id: 所属租户 ID（数据隔离维度之一）。
        """
        ...

    @abstractmethod
    async def get(
        self,
        key: str,
        *,
        user_id: str = DEFAULT_USER_ID,
        tenant_id: str = DEFAULT_TENANT_ID,
    ) -> Optional[Any]:
        """
        获取记忆。

        Args:
            key: 记忆键。
            user_id: 所有者用户 ID，跨作用域读取返回 None。
            tenant_id: 所属租户 ID，跨作用域读取返回 None。

        Returns:
            记忆值，不存在返回 None。
        """
        ...

    @abstractmethod
    async def delete(
        self,
        key: str,
        *,
        user_id: str = DEFAULT_USER_ID,
        tenant_id: str = DEFAULT_TENANT_ID,
    ) -> None:
        """
        删除记忆。

        Args:
            key: 记忆键。
            user_id: 所有者用户 ID。
            tenant_id: 所属租户 ID。
        """
        ...

    @abstractmethod
    async def search(
        self,
        query: str,
        top_k: int = 5,
        *,
        user_id: str = DEFAULT_USER_ID,
        tenant_id: str = DEFAULT_TENANT_ID,
    ) -> list[dict[str, Any]]:
        """
        语义搜索记忆（仅在指定作用域内检索，禁止跨租户/跨用户召回）。

        Args:
            query: 搜索查询文本。
            top_k: 返回最相关的 top_k 条结果。
            user_id: 所有者用户 ID。
            tenant_id: 所属租户 ID。

        Returns:
            记忆列表，每项包含 key、value、score 等。
        """
        ...
