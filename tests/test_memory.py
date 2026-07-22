"""
记忆系统单元测试。
覆盖短期记忆（内存/Redis 降级）和长期记忆（mock embedding + 临时 Chroma）。
"""

import time

import pytest

from app.config.settings import Settings
from app.memory.factory import MemoryFactory
from app.memory.long_term import VectorLongTermMemory
from app.memory.short_term import InMemoryShortTermMemory, RedisShortTermMemory
from app.rag.vector_store import ChromaStore


class TestInMemoryShortTermMemory:
    """进程内短期记忆测试。"""

    @pytest.mark.asyncio
    async def test_save_and_get(self):
        mem = InMemoryShortTermMemory()
        await mem.save("k1", {"a": 1})
        assert await mem.get("k1") == {"a": 1}

    @pytest.mark.asyncio
    async def test_get_missing(self):
        mem = InMemoryShortTermMemory()
        assert await mem.get("nope") is None

    @pytest.mark.asyncio
    async def test_delete(self):
        mem = InMemoryShortTermMemory()
        await mem.save("k1", "v1")
        await mem.delete("k1")
        assert await mem.get("k1") is None

    @pytest.mark.asyncio
    async def test_ttl_expiry(self):
        mem = InMemoryShortTermMemory()
        await mem.save("k1", "v1", ttl=1)
        # 手动过期
        mem._store["k1"] = ("v1", time.time() - 1)
        assert await mem.get("k1") is None

    @pytest.mark.asyncio
    async def test_search_by_prefix(self):
        mem = InMemoryShortTermMemory()
        await mem.save("session:1", "a")
        await mem.save("session:2", "b")
        await mem.save("other", "c")
        results = await mem.search("session", top_k=5)
        keys = {r["key"] for r in results}
        assert "session:1" in keys and "session:2" in keys


class TestRedisShortTermMemoryDegrade:
    """Redis 短期记忆降级到内存测试（无真实 Redis 服务）。"""

    @pytest.mark.asyncio
    async def test_degrade_save_get(self):
        # 指向不可达的 Redis 端口，触发降级
        settings = Settings(REDIS_HOST="127.0.0.1", REDIS_PORT=1)
        mem = RedisShortTermMemory(settings)
        await mem.save("k1", {"x": 1})
        value = await mem.get("k1")
        assert value == {"x": 1}
        # 降级后应标记 degraded
        assert mem._degraded is True

    @pytest.mark.asyncio
    async def test_degrade_delete_and_search(self):
        settings = Settings(REDIS_HOST="127.0.0.1", REDIS_PORT=1)
        mem = RedisShortTermMemory(settings)
        await mem.save("session:a", "v")
        await mem.delete("session:a")
        assert await mem.get("session:a") is None


class TestVectorLongTermMemory:
    """长期记忆测试（mock embedding + 临时 Chroma）。"""

    def _make_memory(self, temp_chroma_dir, mock_embedding_provider):
        store = ChromaStore(temp_chroma_dir)
        return VectorLongTermMemory(
            settings=Settings(),
            embedding_provider=mock_embedding_provider,
            vector_store=store,
        )

    @pytest.mark.asyncio
    async def test_save_and_get(self, temp_chroma_dir, mock_embedding_provider):
        mem = self._make_memory(temp_chroma_dir, mock_embedding_provider)
        await mem.save("pref:1", "用户喜欢简洁的回答")
        assert await mem.get("pref:1") == "用户喜欢简洁的回答"

    @pytest.mark.asyncio
    async def test_save_overwrite(self, temp_chroma_dir, mock_embedding_provider):
        mem = self._make_memory(temp_chroma_dir, mock_embedding_provider)
        await mem.save("pref:1", "第一版")
        await mem.save("pref:1", "第二版")
        assert await mem.get("pref:1") == "第二版"

    @pytest.mark.asyncio
    async def test_search(self, temp_chroma_dir, mock_embedding_provider):
        mem = self._make_memory(temp_chroma_dir, mock_embedding_provider)
        await mem.save("m1", "Python 异步编程")
        await mem.save("m2", "机器学习基础")
        results = await mem.search("Python 异步编程", top_k=2)
        assert len(results) >= 1
        assert any(r["value"] == "Python 异步编程" for r in results)

    @pytest.mark.asyncio
    async def test_delete(self, temp_chroma_dir, mock_embedding_provider):
        mem = self._make_memory(temp_chroma_dir, mock_embedding_provider)
        await mem.save("m1", "内容")
        await mem.delete("m1")
        assert await mem.get("m1") is None


class TestMemoryFactory:
    """记忆工厂测试。"""

    def test_create_short_term_in_memory(self):
        mem = MemoryFactory.create_short_term(Settings(), use_redis=False)
        assert isinstance(mem, InMemoryShortTermMemory)

    def test_create_short_term_redis(self):
        mem = MemoryFactory.create_short_term(Settings(), use_redis=True)
        assert isinstance(mem, RedisShortTermMemory)
