"""
搜索意图识别（时效性搜索模式）。

职责：在 Tavily 调用之前判断查询是否为时效性问题，并据此选择搜索策略
（topic / search_depth / days / 结果数量 / 是否官方优先 / 是否取正文）。

设计：以确定性规则为主（可测试、无额外 LLM 延迟），可选地叠加 LLM 分类
（默认关闭，见 WEB_SEARCH_ENABLE_LLM_INTENT）。所有参数均映射到当前项目
安装的 tavily-python 版本真实支持的 kwargs，不猜测不存在的参数。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

# 时效性问题触发词（命中其一即视为 time_sensitive，并进入对应专用意图）。
_TIME_WORDS: tuple[str, ...] = (
    "最新", "今天", "今日", "当前", "目前", "现在", "近期", "最近",
    "刚刚", "本周", "本月", "今年", "截至", "目前最新", "当前最新",
    "最新版本", "最新公告", "最新政策", "最新价格", "最新状态",
    "是否已经", "是否已", "目前状态", "当前状态", "刚发布", "刚上线",
    "latest", "current", "today", "recent", "recently", "newly",
    "this week", "this month", "right now", "as of now", "now available",
    "new release", "latest version", "latest news",
)
# 新闻类触发词（命中则归类 NEWS，若同时命中版本/官方词则以版本/官方优先）。
_NEWS_WORDS: tuple[str, ...] = (
    "新闻", "消息", "报道", "头条", "快讯", "突发", "发生了什么",
    "news", "headline", "breaking", "update", "发生了什么",
)
# 官方/一手来源触发词（版本、政策、公告、发布、定价等）。
_OFFICIAL_WORDS: tuple[str, ...] = (
    "官方", "公告", "声明", "政策", "法规", "规定", "最新公告", "最新政策",
    "定价", "价格", "发布", "上线", "发布公告", "官方文档", "官方博客",
    "official", "announcement", "release", "policy", "regulation", "pricing",
)
# 版本/发布类触发词（软件版本、API 更新、产品发布）。
_VERSION_WORDS: tuple[str, ...] = (
    "版本", "最新版本", "版本号", "v2", "v3", "v4", "v5", "版", "release",
    "changelog", "更新日志", "是否已发布", "上线了", "新版本",
)


class SearchIntent(str, Enum):
    GENERAL_KNOWLEDGE = "GENERAL_KNOWLEDGE"
    TIME_SENSITIVE = "TIME_SENSITIVE"
    NEWS = "NEWS"
    CURRENT_STATUS = "CURRENT_STATUS"
    OFFICIAL_UPDATE = "OFFICIAL_UPDATE"
    VERSION_RELEASE = "VERSION_RELEASE"


# 意图优先级（越高越"专一"，命中即覆盖低优先级意图）。
_INTENT_PRECEDENCE: tuple[SearchIntent, ...] = (
    SearchIntent.VERSION_RELEASE,
    SearchIntent.OFFICIAL_UPDATE,
    SearchIntent.NEWS,
    SearchIntent.CURRENT_STATUS,
    SearchIntent.TIME_SENSITIVE,
)


@dataclass(frozen=True)
class IntentDecision:
    """一次搜索的意图决策结果。"""

    intent: SearchIntent
    time_sensitive: bool = False
    # 映射到 Tavily 参数（当前 SDK 真实支持）。
    topic: str | None = None          # general / news / finance
    search_depth: str = "basic"       # basic / advanced / fast / ultra-fast
    days: int | None = None           # 近 N 天
    max_results: int = 5              # 候选结果数量
    include_raw_content: bool | str = False  # bool / "markdown" / "text"
    prefer_official: bool = False     # 是否需要官方来源加权
    # 用于可观测性日志的简短说明。
    reason: str = ""
    matched_words: list[str] = field(default_factory=list)


def _has_any(query: str, words: tuple[str, ...]) -> bool:
    return any(w in query for w in words)


def _matched(query: str, words: tuple[str, ...]) -> list[str]:
    return [w for w in words if w in query]


def detect_intent(query: str) -> IntentDecision:
    """基于规则识别查询意图（主路径，无 LLM 开销）。"""
    q = (query or "").strip().lower()
    matched_time = _matched(q, _TIME_WORDS)
    time_sensitive = bool(matched_time)
    matched = list(matched_time)

    # 以"专一度"最高者优先：版本 > 官方 > 新闻 > 当前状态 > 时效敏感。
    has_version = _has_any(q, _VERSION_WORDS)
    has_official = _has_any(q, _OFFICIAL_WORDS)
    has_news = _has_any(q, _NEWS_WORDS)
    has_status = _has_any(q, ("状态", "status", "是否", "现在", "目前", "current"))

    if has_version:
        intent = SearchIntent.VERSION_RELEASE
        matched += _matched(q, _VERSION_WORDS)
    elif has_official:
        intent = SearchIntent.OFFICIAL_UPDATE
        matched += _matched(q, _OFFICIAL_WORDS)
    elif has_news and (time_sensitive or has_news):
        intent = SearchIntent.NEWS
    elif has_status:
        intent = SearchIntent.CURRENT_STATUS
    elif time_sensitive:
        intent = SearchIntent.TIME_SENSITIVE
    else:
        intent = SearchIntent.GENERAL_KNOWLEDGE

    time_sensitive = intent != SearchIntent.GENERAL_KNOWLEDGE
    reason = _build_reason(intent, matched)
    days, depth, topic, max_results, raw, official = _config_for(intent)
    return IntentDecision(
        intent=intent,
        time_sensitive=time_sensitive,
        topic=topic,
        search_depth=depth,
        days=days,
        max_results=max_results,
        include_raw_content=raw,
        prefer_official=official,
        reason=reason,
        matched_words=list(dict.fromkeys(matched)),
    )


def _config_for(intent: SearchIntent):
    """按意图返回 Tavily 检索配置（相对普通查询增加深度/时效/候选数量）。"""
    if intent == SearchIntent.NEWS:
        return 8, "advanced", "news", 10, True, False      # days, depth, topic, max, raw, official
    if intent == SearchIntent.VERSION_RELEASE:
        return 60, "advanced", None, 10, "markdown", True
    if intent == SearchIntent.OFFICIAL_UPDATE:
        return 30, "advanced", None, 10, "markdown", True
    if intent == SearchIntent.CURRENT_STATUS:
        return 14, "advanced", None, 8, True, False
    if intent == SearchIntent.TIME_SENSITIVE:
        return 7, "advanced", None, 8, False, False
    # GENERAL_KNOWLEDGE：普通速度与成本。
    return None, "basic", None, 5, False, False


def _build_reason(intent: SearchIntent, matched: list[str]) -> str:
    if intent == SearchIntent.GENERAL_KNOWLEDGE:
        return "普通知识问题，采用基础检索，不强制实时。"
    if matched:
        return f"命中时效性关键词（{'、'.join(dict.fromkeys(matched))}），归为 {intent.value}。"
    return f"归为 {intent.value}（时效类默认采用高级检索）。"


_RE_TIME_STRONG = re.compile(
    r"(今天|今日|刚刚|本周|本月|当前最新|目前最新|right now|as of now|this week)", re.I
)


def requires_cache_bypass(query: str) -> bool:
    """'今天/刚刚/当前'等强时效问题应绕过缓存，避免复用旧结果。"""
    return bool(_RE_TIME_STRONG.search(query or "")) or "最新版本" in (query or "")
