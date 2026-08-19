"""
Remember — 记忆工具集（memory.get / memory.set / memory.search / memory.delete）。

通过统一的 BaseMemory 抽象读写记忆，Agent Core 不直接依赖具体存储（Redis/内存/向量库）。
- 多用户隔离：所有读写携带调用者 user_id/tenant_id，默认不跨作用域读取。
- 每个操作是独立 Tool，便于权限字符串细分（remember:read / remember:write）。
"""

from __future__ import annotations

from typing import Any

from app.config.logging import get_logger
from app.config.settings import Settings, get_settings
from app.memory.base import BaseMemory
from app.memory.factory import MemoryFactory
from app.tools.base import BaseTool, ToolInput, ToolOutput
from app.tools.schema import ExecutionMode, ToolCategory
from app.tools.security import CATEGORY_SYSTEM, ToolContext

logger = get_logger(__name__)


def _get_memory(settings: Settings | None = None) -> BaseMemory:
    """返回短期记忆实现（Redis 或内存降级，依配置）。"""
    return MemoryFactory.create_short_term(settings or get_settings())


def _scope(context: ToolContext | None) -> tuple[str, str]:
    if context is None:
        return "anonymous", "default"
    return context.user_id or "anonymous", context.tenant_id or "default"


class _MemoryToolBase(BaseTool):
    category: str = CATEGORY_SYSTEM
    runtime_category: ToolCategory = ToolCategory.REMEMBER
    execution_mode: ExecutionMode = ExecutionMode.SYNC
    timeout: float = 5.0

    def __init__(self, settings: Settings | None = None, memory: BaseMemory | None = None):
        self._settings = settings or get_settings()
        # 允许注入 memory 实例（测试/自定义后端），否则惰性创建。
        self._memory: BaseMemory | None = memory

    def _store(self) -> BaseMemory:
        if self._memory is not None:
            return self._memory
        return _get_memory(self._settings)

    # 短期记忆契约：调用方负责把 key 命名空间化（见 BaseMemory 注释），
    # 以 user_id/tenant_id 为作用域前缀，杜绝跨用户/跨租户读取。
    def _scope_ns(self, user_id: str, tenant_id: str) -> str:
        return f"stm:{tenant_id}:{user_id}"

    def _scope_key(self, user_id: str, tenant_id: str, key: str) -> str:
        return f"{self._scope_ns(user_id, tenant_id)}:{key}"

    @property
    def name(self) -> str:
        raise NotImplementedError


class MemoryGetTool(_MemoryToolBase):
    permissions: frozenset[str] = frozenset({"remember:read"})
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {"key": {"type": "string"}},
        "required": ["key"],
    }

    @property
    def name(self) -> str:
        return "memory.get"

    @property
    def description(self) -> str:
        return "读取当前会话/用户作用域内的一条记忆。"

    async def execute(self, input: ToolInput, context: ToolContext | None = None) -> ToolOutput:
        auth_error = self._authorize(context)
        if auth_error:
            return ToolOutput(success=False, error=auth_error)
        params = input.parameters or {}
        key = str(params.get("key") or input.query).strip()
        if not key:
            return ToolOutput(success=False, error="缺少必填参数: key")
        user_id, tenant_id = _scope(context)
        stored_key = self._scope_key(user_id, tenant_id, key)
        value = await self._store().get(stored_key, user_id=user_id, tenant_id=tenant_id)
        if value is None:
            return ToolOutput(success=False, error=f"记忆不存在: {key}")
        return ToolOutput(success=True, data={"key": key, "value": value})


class MemorySetTool(_MemoryToolBase):
    permissions: frozenset[str] = frozenset({"remember:write"})
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "key": {"type": "string"},
            "value": {"type": "string"},
            "ttl": {"type": "integer", "description": "可选，过期秒数"},
        },
        "required": ["key", "value"],
    }

    @property
    def name(self) -> str:
        return "memory.set"

    @property
    def description(self) -> str:
        return "在当前会话/用户作用域内保存一条记忆（key/value）。"

    async def execute(self, input: ToolInput, context: ToolContext | None = None) -> ToolOutput:
        auth_error = self._authorize(context)
        if auth_error:
            return ToolOutput(success=False, error=auth_error)
        params = input.parameters or {}
        key = str(params.get("key") or "").strip()
        value = params.get("value", input.query)
        if not key:
            return ToolOutput(success=False, error="缺少必填参数: key")
        user_id, tenant_id = _scope(context)
        ttl = params.get("ttl")
        stored_key = self._scope_key(user_id, tenant_id, key)
        await self._store().save(
            stored_key, value, ttl=int(ttl) if ttl else None, user_id=user_id, tenant_id=tenant_id
        )
        return ToolOutput(success=True, data={"key": key, "saved": True})


class MemorySearchTool(_MemoryToolBase):
    permissions: frozenset[str] = frozenset({"remember:read"})
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "top_k": {"type": "integer", "default": 5},
        },
        "required": ["query"],
    }

    @property
    def name(self) -> str:
        return "memory.search"

    @property
    def description(self) -> str:
        return "在本用户/租户作用域内语义检索记忆。"

    async def execute(self, input: ToolInput, context: ToolContext | None = None) -> ToolOutput:
        auth_error = self._authorize(context)
        if auth_error:
            return ToolOutput(success=False, error=auth_error)
        params = input.parameters or {}
        query = str(params.get("query") or input.query).strip()
        if not query:
            return ToolOutput(success=False, error="缺少必填参数: query")
        user_id, tenant_id = _scope(context)
        top_k = int(params.get("top_k") or 5)
        results = await self._store().search(
            query, top_k=top_k, user_id=user_id, tenant_id=tenant_id
        )
        ns = self._scope_ns(user_id, tenant_id) + ":"
        scoped = []
        for r in results:
            k = r.get("key", "")
            if k.startswith(ns):  # 仅返回当前用户/租户作用域内的结果
                scoped.append({**r, "key": k[len(ns):]})
            if len(scoped) >= top_k:
                break
        return ToolOutput(success=True, data={"results": scoped, "count": len(scoped)})


class MemoryDeleteTool(_MemoryToolBase):
    permissions: frozenset[str] = frozenset({"remember:write"})
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {"key": {"type": "string"}},
        "required": ["key"],
    }

    @property
    def name(self) -> str:
        return "memory.delete"

    @property
    def description(self) -> str:
        return "删除当前用户/租户作用域内的一条记忆。"

    async def execute(self, input: ToolInput, context: ToolContext | None = None) -> ToolOutput:
        auth_error = self._authorize(context)
        if auth_error:
            return ToolOutput(success=False, error=auth_error)
        params = input.parameters or {}
        key = str(params.get("key") or input.query).strip()
        if not key:
            return ToolOutput(success=False, error="缺少必填参数: key")
        user_id, tenant_id = _scope(context)
        stored_key = self._scope_key(user_id, tenant_id, key)
        await self._store().delete(stored_key, user_id=user_id, tenant_id=tenant_id)
        return ToolOutput(success=True, data={"key": key, "deleted": True})
