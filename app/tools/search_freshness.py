"""
搜索结果新鲜度评分与来源质量排序（Tavily 与 LLM 之间的处理层）。

对每个结果解析结构化元数据（title/url/domain/published_date/updated_date/content），
计算 relevance / freshness / source_quality 三维度并生成综合分数。

- 时效性问题：综合分 = relevance × freshness 加权 × source_quality 加权（官方来源可再增强）。
- 普通知识问题：不过度惩罚旧内容，主要保留 relevance + 来源质量。
- 对无发布日期的结果，freshness 取较低默认值，不默认视为最新。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from urllib.parse import urlparse

from app.tools.search_intent import SearchIntent

# 低质量来源路径提示（聚合/论坛/内容农场等）。通用规则，避免硬编码具体站点。
_LOW_QUALITY_PATH_HINTS: tuple[str, ...] = (
    "/forum", "/forums", "/threads", "/answers", "/question",
    "/redirect?", "/aggregator", "/toplist", "/list/contents",
)
_OFFICIAL_PATH_HINTS: tuple[str, ...] = (
    "/docs", "/documentation", "/release", "/releases", "/changelog",
    "/announcement", "/newsroom", "/press", "/blog/official", "/support",
)
_STYLE_HOME_PREFIXES: tuple[str, ...] = (
    "ja.", "zh.", "en.", "dev.", "www.", "developer.",
)

# 未知日期 → freshness 默认低值（不可当作最新）。
_FRESHNESS_UNKNOWN: float = 0.35

_DOMAIN_RE: re.Pattern[str] = re.compile(r"^[a-z0-9.-]+$")


def parse_date(value: object) -> date | None:
    """尽量从 Tavily 返回的日期字段解析 date；失败返回 None（不伪造）。"""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", text)
        if m:
            try:
                return date(int(m[1]), int(m[2]), int(m[3]))
            except ValueError:
                return None
    return None


def normalize_domain(url: str) -> str:
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return ""
    for prefix in _STYLE_HOME_PREFIXES:
        if host.startswith(prefix) and len(host) > len(prefix):
            return host[len(prefix):]
    return host


def days_ago(value: object, today: date | None = None) -> int | None:
    """结果发布距今的天数；无法解析返回 None。"""
    d = parse_date(value)
    if d is None:
        return None
    today = today or date.today()
    return max(0, (today - d).days)


def freshness_score(value: object, today: date | None = None) -> float:
    d = parse_date(value)
    if d is None:
        return _FRESHNESS_UNKNOWN
    days = days_ago(value, today)
    if days is None:
        return _FRESHNESS_UNKNOWN
    if days == 0:
        return 1.0
    if days <= 3:
        return 0.95
    if days <= 7:
        return 0.85
    if days <= 30:
        return 0.6
    return 0.3


def _in_lists(domain: str, lists) -> str | None:
    for lst in lists:
        for entry in lst:
            if not entry:
                continue
            if domain == entry or domain.endswith("." + entry.lstrip(".")):
                return entry
    return None


def source_quality(
    url: str,
    *,
    official_domains=None,
    trusted_domains=None,
    low_quality_domains=None,
) -> float:
    """来源质量分（0~1）。官方一手 > 可信 > 普通 > 低质聚合/内容农场。"""
    official_domains = official_domains or []
    trusted_domains = trusted_domains or []
    low_quality_domains = low_quality_domains or []
    domain = normalize_domain(url)
    path = (urlparse(url).path or "").lower()

    low = _in_lists(domain, [low_quality_domains])
    if low or any(h in path for h in _LOW_QUALITY_PATH_HINTS):
        return 0.2

    official = _in_lists(domain, [official_domains])
    if official or any(p in path for p in _OFFICIAL_PATH_HINTS):
        return 1.0
    if domain in ("github.com", "gitee.com") and any(
        p in path for p in ("/releases", "/blob", "/tags")
    ):
        return 0.95
    if domain.endswith(".gov") or domain.endswith(".gov.cn") or domain.endswith(".edu.cn"):
        return 0.95

    if _in_lists(domain, [trusted_domains]):
        return 0.85
    return 0.6


@dataclass
class ScoredSource:
    rank: int
    title: str
    url: str
    domain: str
    published_date: str
    updated_date: str
    content: str
    source_type: str  # official / trusted / third_party / low_quality
    is_official: bool
    raw_score: float          # Tavily 原生相关性分数（若提供）
    relevance: float
    freshness: float
    source_quality: float
    combined: float

    def to_marker_block(self) -> str:
        """生成带元数据的 [WEB SOURCE] 文本块，供 LLM 判断新旧与权威性。"""
        date_line = self.published_date or self.updated_date or "unknown"
        stype = {
            "official": "Official",
            "trusted": "Trusted",
            "third_party": "Third-party",
            "low_quality": "Low-Quality",
        }.get(self.source_type, "Unknown")
        freshness = {
            1.0: "News (today)",
            0.95: "High",
            0.85: "Medium-High",
            0.6: "Medium",
            0.35: "Low",
            0.3: "Low",
        }.get(round(self.freshness, 2), f"{self.freshness:.2f}")
        lines = [
            f"[WEB SOURCE #{self.rank}]",
            f"Source Date: {date_line}",
            f"Source Type: {stype}",
            f"Freshness: {freshness}",
            f"URL: {self.url}",
            f"Relevance: {self.relevance:.2f}",
            "",
            self.content.strip(),
        ]
        return "\n".join(lines)


def _relevance_of(item: dict, base_score: float | None) -> float:
    if isinstance(base_score, (int, float)) and base_score is not None:
        return max(0.0, min(1.0, float(base_score) / 1.0 if base_score <= 1 else 0.0))
    return 0.5


def score_and_rank(
    items: list[dict],
    intent: SearchIntent,
    *,
    official_domains=None,
    trusted_domains=None,
    low_quality_domains=None,
    today: date | None = None,
    prefer_official: bool = False,
) -> list[ScoredSource]:
    """对 Tavily 原始结果评分并按综合分降序排序。"""
    today = today or date.today()
    processed: list[ScoredSource] = []
    for idx, item in enumerate(items, start=1):
        title = str(item.get("title") or "").strip()
        url = str(item.get("url") or "").strip()
        content = item.get("content") or item.get("raw_content") or ""
        if isinstance(content, bytes):
            content = content.decode("utf-8", errors="replace")
        domain = normalize_domain(url)
        published = parse_date(item.get("published_date") or item.get("date"))
        updated = parse_date(item.get("updated_date"))
        raw_score = item.get("score")

        relevance = _relevance_of(item, raw_score)

        # 时效性：普遍采用 freshness 乘子；普通问题则不惩罚旧内容。
        quality = source_quality(
            url,
            official_domains=official_domains,
            trusted_domains=trusted_domains,
            low_quality_domains=low_quality_domains,
        )
        is_official = quality >= 0.95
        freshness = freshness_score(published or updated, today)

        if intent != SearchIntent.GENERAL_KNOWLEDGE:
            combined = relevance * (0.35 + 0.65 * freshness) * (0.35 + 0.65 * quality)
            if prefer_official and is_official:
                combined *= 1.15
        else:
            # 普通知识：主要强调相关性与来源质量，旧内容不过度受罚。
            combined = relevance * (0.5 + 0.5 * quality)

        if combined <= 0:
            combined = 1e-3

        source_type = (
            "official" if is_official
            else "trusted" if quality >= 0.85
            else "low_quality" if quality <= 0.25
            else "third_party"
        )
        pub = published or updated
        published_date = pub.isoformat() if pub else ""
        processed.append(
            ScoredSource(
                rank=0,
                title=title,
                url=url,
                domain=domain,
                published_date=published_date,
                updated_date=updated.isoformat() if updated else "",
                content=str(content)[:4000],
                source_type=source_type,
                is_official=is_official,
                raw_score=round(raw_score, 4) if isinstance(raw_score, (int, float)) else 0.0,
                relevance=round(relevance, 3),
                freshness=round(freshness, 3),
                source_quality=round(quality, 3),
                combined=round(combined, 4),
            )
        )

    processed.sort(key=lambda s: (s.combined, s.freshness), reverse=True)
    for rank, s in enumerate(processed, start=1):
        s.rank = rank
    return processed


def evidence_cutoff(sp: list[ScoredSource]) -> str:
    """最新可信来源的日期边界（用于'截至'表述）；无日期返回空串（表示无法确认）。"""
    newest = None
    for s in sp:
        d = parse_date(s.published_date or s.updated_date)
        if d and s.source_type in ("official", "trusted", "third_party"):
            if newest is None or d > newest:
                newest = d
    return newest.isoformat() if newest else ""


def is_stale(
    sp: list[ScoredSource],
    *,
    max_days: int = 14,
    today: date | None = None,
) -> bool:
    """
    判断检索证据是否可能过时：最新来源距今超过 max_days 且剩余来源同样陈旧。
    用于 LLM '证据不足'提示（不做'截至今天'断言）。
    """
    today = today or date.today()
    dates = [d for d in (parse_date(s.published_date or s.updated_date) for s in sp) if d]
    if not dates:
        return True
    newest = max(dates)
    return (today - newest).days > max_days
