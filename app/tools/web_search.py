"""
Web 搜索工具（时效性搜索模式）。

链路：query → 意图识别 → 缓存判定 → Tavily(按意图参数) → 结果处理 →
新鲜度 + 来源质量排序 → 带元数据的 [WEB SOURCE] 上下文 → LLM。

改进点（相对旧实现）：
- 按查询意图自动选择 Tavily topic / search_depth / days / 结果数量 / 是否取正文。
- 保留并输出 published/updated 日期、相关性分、官方/可信/低质来源类型，供 LLM 判断新旧与权威性。
- 时效性/新闻采用短缓存，'今天/刚刚/当前'等强时效问题绕过缓存；普通知识可较长缓存。
- 输出 [SEARCH SUMMARY] 与 [WEB SOURCE] 结构化块，并在证据可能过时时显式提示。
- 结构化可观测日志：intent/cache_hit/结果数/最新日期/官方来源数/平均新鲜度等。

只使用当前项目安装的 tavily-python 版本真实支持的参数，不猜测不存在的参数。
"""

from __future__ import annotations

from typing import Any

from app.config.logging import get_logger
from app.config.settings import Settings, get_settings
from app.tools.base import BaseTool, ToolInput, ToolOutput
from app.tools.search_cache import get_search_cache
from app.tools.search_freshness import (
    ScoredSource,
    evidence_cutoff,
    is_stale,
    score_and_rank,
)
from app.tools.search_intent import detect_intent, requires_cache_bypass
from app.tools.security import CATEGORY_NETWORK, ToolContext

logger = get_logger(__name__)


def _default_client_factory(api_key: str):
    from tavily import TavilyClient  # 惰性导入，未安装/未配置时路径仍可用

    return TavilyClient(api_key=api_key)


class WebSearchTool(BaseTool):
    """基于 Tavily 的联网搜索工具，具备时效性搜索模式。"""

    category: str = CATEGORY_NETWORK
    required_params: list[str] = ["query"]

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        client_factory=None,
        search_cache=None,
    ):
        self._settings = settings or get_settings()
        self._client_factory = client_factory or _default_client_factory
        self._cache = search_cache or get_search_cache()

    @property
    def name(self) -> str:
        return "web_search"

    @property
    def description(self) -> str:
        return (
            "联网搜索实时信息。输入搜索关键词或问题，返回相关网页的标题、摘要、"
            "链接与发布日期。适合查询最新新闻、版本发布、政策公告、实时状态等"
            "时效性问题；也可用于普通事实查询。"
        )

    def _intent_and_cache(self, query: str):
        decision = detect_intent(query)
        bypass = requires_cache_bypass(query)
        if decision.intent.name == "GENERAL_KNOWLEDGE":
            max_age = float(self._settings.WEB_SEARCH_CACHE_TTL)
        elif bypass:
            max_age = 0.0
        else:
            max_age = float(self._settings.WEB_SEARCH_TIME_SENSITIVE_CACHE_TTL)
        return decision, max_age

    def _build_query_params(self, decision) -> dict[str, Any]:
        """按意图构建 Tavily 搜索参数（见 search_intent._config_for）。"""
        params: dict[str, Any] = {
            "max_results": decision.max_results,
            "search_depth": decision.search_depth,
        }
        if decision.topic is not None:
            params["topic"] = decision.topic
        if decision.days is not None:
            params["days"] = decision.days
        if decision.include_raw_content:
            params["include_raw_content"] = decision.include_raw_content
        return params

    async def _search_tavily(self, query: str, params: dict[str, Any]) -> list[dict]:
        api_key = self._settings.TAVILY_API_KEY
        client = self._client_factory(api_key)
        response = client.search(query=query, **params)
        return response.get("results", []) if isinstance(response, dict) else []

    def _require_result_items(self, decision, raw: list[dict]) -> list[ScoredSource]:
        return score_and_rank(
            raw,
            decision.intent,
            official_domains=self._settings.WEB_SEARCH_OFFICIAL_DOMAINS,
            trusted_domains=self._settings.WEB_SEARCH_TRUSTED_DOMAINS,
            low_quality_domains=self._settings.WEB_SEARCH_LOW_QUALITY_DOMAINS,
            prefer_official=decision.prefer_official,
        )

    def _format_output(self, decision, scored: list[ScoredSource]) -> str:
        output_limit = max(1, self._settings.WEB_SEARCH_MAX_RESULTS)
        selected = scored[:output_limit]
        newest = evidence_cutoff(scored)
        stale = is_stale(scored, max_days=14)
        lines: list[str] = []
        lines.append(f"[SEARCH INTENT] {decision.intent.value}")
        if newest:
            lines.append(f"[EVIDENCE] newest_source_date={newest}")
        if stale:
            lines.append(
                "[EVIDENCE] possibly_stale=true  "
                "(检索到的最新来源距今较久/无法建立当前状态；请勿声称截止今天)"
            )
        if decision.prefer_official:
            lines.append(
                "[EVIDENCE] official_priority=true (版本/官方类问题：优先官方/一手来源)"
            )
        lines.append("")
        for item in selected:
            lines.append(item.to_marker_block())
            lines.append("")
        return "\n".join(lines).rstrip()

    def _observability(
        self,
        *,
        query: str,
        intent: str,
        topic: str | None,
        params: dict[str, Any],
        cache_hit: bool,
        scored: list[ScoredSource],
    ) -> None:
        newest = evidence_cutoff(scored)
        top = scored[0] if scored else None
        official_count = sum(1 for s in scored if s.is_official)
        avg_freshness = round(
            sum(s.freshness for s in scored) / len(scored), 3
        ) if scored else 0.0
        selected = [
            {
                "rank": s.rank,
                "url": s.url,
                "date": s.published_date or s.updated_date,
                "type": s.source_type,
            }
            for s in scored[:5]
        ]
        logger.info(
            "web_search",
            query=query,
            intent=intent,
            search_mode=topic or "general",
            tavily_parameters=params,
            cache_hit=cache_hit,
            result_count=len(scored),
            top_result_date=top.published_date or top.updated_date if top else None,
            newest_result_date=newest,
            official_source_count=official_count,
            average_freshness=avg_freshness,
            selected_sources=selected,
        )

    async def execute(
        self,
        input: ToolInput,
        context: ToolContext | None = None,
    ) -> ToolOutput:
        auth_error = self._authorize(context)
        if auth_error:
            return ToolOutput(success=False, error=auth_error)

        query = input.query.strip()
        if not query:
            return ToolOutput(success=False, error="搜索关键词为空")

        if not self._settings.TAVILY_API_KEY:
            return ToolOutput(success=False, error="未配置 TAVILY_API_KEY")

        decision, max_age = self._intent_and_cache(query)
        cache_key = f"{decision.intent.value}|{query.lower()}"
        cached = self._cache.get(cache_key, max_age)
        if cached is not None:
            text = cached["text"]
            self._observability(
                query=query,
                intent=decision.intent.value,
                topic=decision.topic,
                params=self._build_query_params(decision),
                cache_hit=True,
                scored=cached["scored"],
            )
            return ToolOutput(success=True, data=text)

        try:
            params = self._build_query_params(decision)
            raw = await self._search_tavily(query, params)
        except Exception as e:  # pragma: no cover - 依赖网络/外部服务
            logger.warning("搜索失败", error=str(e), query=query, intent=decision.intent.value)
            return ToolOutput(success=False, error=f"搜索失败: {str(e)}")

        if not raw:
            self._observability(
                query=query,
                intent=decision.intent.value,
                topic=decision.topic,
                params=params,
                cache_hit=False,
                scored=[],
            )
            return ToolOutput(success=True, data="未找到相关结果。")

        scored = self._require_result_items(decision, raw)
        text = self._format_output(decision, scored)
        self._cache.set(cache_key, {"text": text, "scored": scored})
        self._observability(
            query=query,
            intent=decision.intent.value,
            topic=decision.topic,
            params=params,
            cache_hit=False,
            scored=scored,
        )
        return ToolOutput(success=True, data=text)
