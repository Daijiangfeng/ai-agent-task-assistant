"""Tavily 联网搜索时效性优化测试。"""

from __future__ import annotations

from datetime import date

import pytest

from app.config.settings import Settings
from app.tools.base import ToolInput
from app.tools.search_cache import SearchResultCache
from app.tools.search_freshness import (
    freshness_score,
    is_stale,
    normalize_domain,
    parse_date,
    score_and_rank,
)
from app.tools.search_intent import SearchIntent, detect_intent, requires_cache_bypass
from app.tools.web_search import WebSearchTool

TODAY = date(2026, 8, 19)


class FakeTavily:
    def __init__(self, results):
        self.results = results
        self.calls = []
        self.last_kwargs = None

    def search(self, query, **kwargs):
        self.calls.append((query, kwargs))
        self.last_kwargs = kwargs
        return {"results": self.results}


def _item(title, url="https://example.com/a", content="内容",
          published=None, score=0.9):
    d = {"title": title, "url": url, "content": content, "score": score}
    if published:
        d["published_date"] = published
    return d


class TestIntentDetection:
    """Case 3：普通知识不强制实时；时效/新闻/版本/官方触发专用模式。"""

    def test_general_knowledge_is_not_time_sensitive(self):
        d = detect_intent("什么是 Transformer？")
        assert d.intent == SearchIntent.GENERAL_KNOWLEDGE
        assert d.time_sensitive is False
        assert d.topic is None
        assert d.search_depth == "basic"
        assert d.max_results == 5
        assert d.prefer_official is False

    def test_news_query(self):
        d = detect_intent("某公司的最新消息是什么？")
        assert d.intent == SearchIntent.NEWS
        assert d.time_sensitive is True
        assert d.topic == "news"
        assert d.search_depth == "advanced"

    def test_version_release_query(self):
        d = detect_intent("ChatGPT 最新版本现在是什么？")
        assert d.intent == SearchIntent.VERSION_RELEASE
        assert d.time_sensitive is True
        assert d.prefer_official is True

    def test_current_status_query(self):
        d = detect_intent("目前 XX 软件的当前状态如何？")
        assert d.intent == SearchIntent.CURRENT_STATUS
        assert d.time_sensitive is True

    def test_time_sensitive_trigger_words(self):
        for q in ("今天的新闻", "本周政策", "最新公告是什么"):
            assert detect_intent(q).time_sensitive is True, q

    def test_cache_bypass_for_today(self):
        assert requires_cache_bypass("今天发生了什么") is True
        assert requires_cache_bypass("最新版本是几") is True
        assert requires_cache_bypass("什么是神经网络") is False


class TestFreshness:
    def test_parse_date(self):
        assert parse_date("2026-08-19") == date(2026, 8, 19)
        assert parse_date("2026-08-19T10:00:00Z") == date(2026, 8, 19)
        assert parse_date("2026-08-19 08:00") == date(2026, 8, 19)
        assert parse_date("n/a") is None
        assert parse_date(None) is None

    def test_freshness_buckets(self):
        assert freshness_score("2026-08-19", TODAY) == 1.0      # 今天
        assert freshness_score("2026-08-17", TODAY) == 0.95     # 1~3 天
        assert freshness_score("2026-08-12", TODAY) == 0.85     # 4~7 天
        assert freshness_score("2026-08-01", TODAY) == 0.6      # 8~30 天
        assert freshness_score("2026-07-01", TODAY) == 0.3      # >30 天
        assert freshness_score(None, TODAY) == 0.35             # 未知不得视为最新

    def test_normalize_domain(self):
        assert normalize_domain("https://www.openai.com/blog") == "openai.com"
        assert normalize_domain("https://docs.python.org/3/") == "docs.python.org"

    def test_all_results_stale_flags_evidence(self):
        """Case 4：全是 2026-07 的旧结果，判定证据可能过时。"""
        raw = [
            _item("a", published="2026-07-01"),
            _item("b", published="2026-07-05"),
            _item("c", published="2026-07-20"),
        ]
        scored = score_and_rank(raw, SearchIntent.TIME_SENSITIVE, today=TODAY)
        assert is_stale(scored, max_days=14, today=TODAY) is True

    def test_official_new_source_outranks_old_third_party(self):
        """Case 5：官方 08-18 应优先于第三方 07-20（版本类问题）。"""
        raw = [
            _item("old-high-relevance", url="https://blog.example.com/x",
                  published="2026-07-20", score=0.99),
            _item("official-release", url="https://openai.com/blog/release",
                  published="2026-08-18", score=0.8),
        ]
        scored = score_and_rank(
            raw,
            SearchIntent.VERSION_RELEASE,
            official_domains=["openai.com"],
            today=TODAY,
            prefer_official=True,
        )
        assert scored[0].title == "official-release"
        assert scored[0].is_official is True

    def test_new_vs_old_conflict_prefers_newer_official(self):
        """Case 6：新官方来源与旧来源冲突时，优先更可信且更新的一手来源。"""
        raw = [
            _item("old-claim", url="https://example.com/x",
                  published="2026-07-01", score=0.95),
            _item("official-new", url="https://openai.com/news/update",
                  published="2026-08-18", score=0.9),
        ]
        scored = score_and_rank(
            raw,
            SearchIntent.VERSION_RELEASE,
            official_domains=["openai.com"],
            today=TODAY,
            prefer_official=True,
        )
        assert scored[0].title == "official-new"

    def test_marker_block_keeps_metadata(self):
        raw = [_item("x", url="https://openai.com/blog/r",
                     published="2026-08-18", score=0.9)]
        scored = score_and_rank(raw, SearchIntent.VERSION_RELEASE,
                                official_domains=["openai.com"], today=TODAY)
        block = scored[0].to_marker_block()
        assert "[WEB SOURCE" in block
        assert "Source Date: 2026-08-18" in block
        assert "Source Type: Official" in block
        assert "Freshness: High" in block


class TestWebSearchTool:
    def _tool(self, fake: FakeTavily, cache=None, **settings_extra):
        settings = Settings(TAVILY_API_KEY="dummy", **settings_extra)
        return WebSearchTool(
            settings,
            client_factory=lambda _key: fake,
            search_cache=cache or SearchResultCache(),
        )

    @pytest.mark.asyncio
    async def test_case1_latest_news_includes_date(self):
        """Case 1：最新消息走 NEWS，输出含源日期。"""
        fake = FakeTavily([
            _item("标题A", url="https://news.example.com/a",
                  published="2026-08-18", score=0.9),
        ])
        tool = self._tool(fake)
        out = await tool.execute(ToolInput(query="某公司的最新消息是什么？"))
        assert out.success is True
        assert "[SEARCH INTENT] NEWS" in out.data
        assert "Source Date: 2026-08-18" in out.data

    @pytest.mark.asyncio
    async def test_case2_version_uses_advanced_official_and_stale_flag(self):
        """Case 2：最新版本走 VERSION_RELEASE + 官方优先，旧证据标 possibly_stale。"""
        fake = FakeTavily([
            _item("旧版文章", url="https://example.com/a",
                  published="2026-07-20", score=0.9),
        ])
        tool = self._tool(fake)
        out = await tool.execute(ToolInput(query="某软件现在最新版本是什么？"))
        assert out.success is True
        assert "[SEARCH INTENT] VERSION_RELEASE" in out.data
        assert "official_priority=true" in out.data
        assert "possibly_stale=true" in out.data
        assert "newest_source_date=2026-07-20" in out.data

    @pytest.mark.asyncio
    async def test_case3_general_keeps_low_cost(self):
        """Case 3：普通知识用 basic 深度、5 条、无 days/topic。"""
        fake = FakeTavily([_item("r", published="2026-08-10")])
        tool = self._tool(fake)
        out = await tool.execute(ToolInput(query="什么是 Transformer？"))
        assert out.success is True
        kwargs = fake.last_kwargs
        assert kwargs["search_depth"] == "basic"
        assert kwargs["max_results"] == 5
        assert "topic" not in kwargs
        assert "days" not in kwargs

    @pytest.mark.asyncio
    async def test_general_query_is_cached(self):
        cache = SearchResultCache()
        fake = FakeTavily([_item("r")])
        tool = self._tool(fake, cache=cache)
        await tool.execute(ToolInput(query="什么是神经网络？"))
        await tool.execute(ToolInput(query="什么是神经网络？"))
        assert len(fake.calls) == 1  # 命中缓存，未再次调用 Tavily

    @pytest.mark.asyncio
    async def test_today_query_bypasses_cache(self):
        cache = SearchResultCache()
        fake = FakeTavily([_item("r", published="2026-08-19")])
        tool = self._tool(fake, cache=cache)
        await tool.execute(ToolInput(query="今天发生了什么？"))
        await tool.execute(ToolInput(query="今天发生了什么？"))
        assert len(fake.calls) == 2  # 强时效问题绕过缓存

    @pytest.mark.asyncio
    async def test_no_results_message(self):
        fake = FakeTavily([])
        tool = self._tool(fake)
        out = await tool.execute(ToolInput(query="最新消息"))
        assert out.success is True
        assert "未找到相关结果" in out.data
