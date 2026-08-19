"""
通用需求提取与工具参数完整性检查。

需求（Requirement）是从用户输入中确定性提取的结构化参数槽位（slot），
例如 location / date / time / budget / destination 等。每个工具可声明其
必填参数（input_schema.required 或 required_params），在调用工具前做
完整性检查：

    User Input
    → Requirement Extraction（确定性规则）
    → Required Parameter Check（工具声明 + 上下文合并）
    → 缺失 → 禁止调用工具 → 向用户询问缺失参数
    → 完整 → 允许调用工具

设计原则：
- 通用机制：槽位注册表与检查逻辑与具体业务解耦，可复用于任意工具；
- 确定性约束：缺失参数由程序识别并阻断，不依赖 Prompt 提示模型；
- 不猜测：缺失参数不得用 unknown/null/空字符串填充后继续调用；
- 不重复询问：已从上下文（对话/已提取参数/工具参数）获取的槽位不再询问。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RequirementSlot:
    """一个可提取的需求参数槽位。"""

    name: str
    label: str  # 中文标签（用于向用户询问）
    patterns: tuple[str, ...] = ()  # 提取正则（首个捕获组为值）
    # 该槽位对哪些工具是必需的（"*" 表示所有工具）
    required_for_tools: frozenset[str] = frozenset()


# 通用需求槽位注册表（确定性正则提取，中文优先）。
# 槽位提取必须保守：宁可漏提，不可误提（误提会把缺失参数伪装成已提供）。
REQUIREMENT_SLOTS: list[RequirementSlot] = [
    RequirementSlot(
        name="location",
        label="城市/区域",
        patterns=(
            r"(?:在|去|到)([^，。；,;]{1,12}?)(?:找|搜索|查|推荐|预定|预订|吃|玩|旅游|旅行|游玩|用餐|餐厅|饭店|景点|酒店|住宿|逛)",
            r"([\u4e00-\u9fff]{2,6}(?:市|区|县|镇))",
            r"([\u4e00-\u9fff]{2,6}?)(?:有哪些|有什么好|哪里好玩|有什么推荐)",
        ),
        required_for_tools=frozenset({"web_search"}),
    ),
    RequirementSlot(
        name="destination",
        label="目的地",
        patterns=(
            r"去([^，。；,;]{1,12}?)(?=，|,|。|；|;|出发|玩|旅游|旅行|出差|度假|$)",
            r"目的地[是为：:：\s]*([^，。；,;]{1,12})",
        ),
    ),
    RequirementSlot(
        name="departure_time",
        label="出发时间",
        patterns=(
            r"([^，。；,;]{1,12}?)(?:出发|启程|动身)",
        ),
    ),
    RequirementSlot(
        name="return_time",
        label="返回时间",
        patterns=(
            r"([^，。；,;]{1,12}?)(?:回来|返回|回程|返程)",
        ),
    ),
    RequirementSlot(
        name="date",
        label="日期",
        patterns=(
            r"(今天|明天|后天|昨天|大后天|周[一二三四五六日天]|星期[一二三四五六日天]|下周[一二三四五六日天]|上周末|本周末|周末|\d{1,2}月\d{1,2}日|\d{4}年\d{1,2}月\d{1,2}日)",
        ),
    ),
    RequirementSlot(
        name="time",
        label="时间",
        patterns=(
            r"(早上|上午|中午|下午|晚上|傍晚|凌晨|早晨|\d{1,2}[:：]\d{2})",
        ),
    ),
    RequirementSlot(
        name="people",
        label="人数",
        patterns=(
            r"([一二两三四五六七八九十\d]+)\s*(?:个人|人|位)",
        ),
    ),
    RequirementSlot(
        name="budget",
        label="预算",
        patterns=(
            r"预算[是为：:：\s]*([\d.]+)\s*元",
            r"([\d.]+)\s*元(?:以内|以下|左右|的预算|预算)",
            r"([\d.]+)\s*块钱",
        ),
    ),
]

# 槽位名 -> 中文标签（供工具必填参数映射）
SLOT_LABELS: dict[str, str] = {s.name: s.label for s in REQUIREMENT_SLOTS}

# 依赖"位置"的查询意图提示词（用于 web_search 的 location 规则）。
# 通用机制：任何"找地点/推荐/附近"类查询都要求提供位置，而非针对餐厅硬编码。
LOCATION_DEPENDENT_HINTS: tuple[str, ...] = (
    "餐厅",
    "饭店",
    "美食",
    "小吃",
    "景点",
    "旅游",
    "旅行",
    "游玩",
    "酒店",
    "民宿",
    "住宿",
    "天气",
    "附近",
    "周边",
    "哪里",
    "哪儿",
    "在哪",
    "在哪儿",
    "攻略",
    "路线",
    "门票",
    "咖啡",
    "奶茶",
    "商场",
    "逛街",
    "好玩",
    "好吃",
    "推荐",
    "打卡",
)

# 无效占位值：不允许当作有效参数继续调用工具
_INVALID_VALUES = {"unknown", "null", "none", "n/a", "na", "待定", "未知", "无"}


def _is_valid(value: Any) -> bool:
    """判断槽位值是否有效（不允许 unknown/null/空字符串）。"""
    if value is None:
        return False
    text = str(value).strip()
    if not text:
        return False
    return text.lower() not in _INVALID_VALUES


def extract_requirements(text: str) -> dict[str, str]:
    """从文本中确定性提取需求槽位（每个槽位首个命中优先）。"""
    if not text:
        return {}
    extracted: dict[str, str] = {}
    for slot in REQUIREMENT_SLOTS:
        for pattern in slot.patterns:
            m = re.search(pattern, text)
            if m:
                value = m.group(1).strip() if m.lastindex else m.group(0).strip()
                if _is_valid(value):
                    extracted[slot.name] = value
                    break
    return extracted


def merge_requirements(*sources: dict[str, Any] | None) -> dict[str, str]:
    """合并多来源需求（后出现的非空值覆盖）。"""
    merged: dict[str, str] = {}
    for src in sources:
        if not src:
            continue
        for key, value in src.items():
            if _is_valid(value):
                merged[key] = str(value).strip()
    return merged


def is_location_dependent_query(query: str) -> bool:
    """判断查询是否为"依赖位置"的意图（通用规则，非业务硬编码）。"""
    return any(hint in query for hint in LOCATION_DEPENDENT_HINTS)


def _tool_required_params(tool_name: str) -> list[str]:
    """工具声明的必填参数（input_schema.required + required_params）。"""
    from app.tools.registry import ToolRegistry

    tool = ToolRegistry.get(tool_name)
    if tool is None:
        return []
    schema = tool.to_schema()
    required: list[str] = []
    input_schema = schema.input_schema or {}
    required.extend(input_schema.get("required") or [])
    required.extend(getattr(schema, "required_params", None) or [])
    return required


def _extract_query(args: dict[str, Any]) -> str:
    """从工具参数中提取自由文本 query。"""
    query = str(args.get("query") or "")
    if not query:
        params = args.get("parameters")
        if isinstance(params, dict):
            query = str(params.get("query") or "")
    return query


@dataclass
class RequirementCheckResult:
    """工具参数完整性检查结果。"""

    allowed: bool
    missing: list[str]  # 缺失槽位名
    labels: list[str]  # 缺失槽位的中文标签（用于询问）

    @property
    def question(self) -> str:
        if not self.missing:
            return ""
        return "请问" + "、".join(self.labels) + "？"


def check_tool_requirements(
    tool_name: str,
    tool_args: dict[str, Any] | None,
    *,
    extracted: dict[str, Any] | None = None,
    conversation: str = "",
) -> RequirementCheckResult:
    """
    检查调用某工具前是否满足参数完整性要求（确定性阻断）。

    可用参数来源（合并去重）：
    1. 本次工具调用已显式提供的具名参数；
    2. 已提取的需求槽位（extracted_requirements）；
    3. 从本次 query 与对话历史中再次确定性提取。

    缺失判定：
    - 工具声明的必填参数（input_schema.required / required_params）不在可用集合；
    - 槽位注册表中声明 required_for_tools 的槽位不在可用集合
      （web_search 的 location 仅对"依赖位置"的查询强制）。
    """
    extracted = extracted or {}
    args = dict(tool_args or {})
    query = _extract_query(args)

    # 合并所有可用来源
    available = merge_requirements(
        extracted,
        extract_requirements(query),
        extract_requirements(conversation),
    )
    # 工具参数中显式提供的具名参数也算可用
    for key, value in args.items():
        if key in ("query", "parameters"):
            continue
        if _is_valid(value):
            available.setdefault(key, str(value))

    missing: list[str] = []
    labels: list[str] = []

    # 1) 工具声明的必填参数
    for param in _tool_required_params(tool_name):
        if param in ("query", "parameters"):
            continue
        if param not in available:
            missing.append(param)
            labels.append(SLOT_LABELS.get(param, param))

    # 2) 槽位注册表 required_for_tools 声明
    for slot in REQUIREMENT_SLOTS:
        if tool_name not in slot.required_for_tools and "*" not in slot.required_for_tools:
            continue
        if (
            slot.name == "location"
            and tool_name == "web_search"
            and not is_location_dependent_query(query)
        ):
            continue
        if slot.name not in available:
            missing.append(slot.name)
            labels.append(slot.label)

    # 去重（保持顺序）
    seen: set[str] = set()
    unique_missing: list[str] = []
    unique_labels: list[str] = []
    for name, label in zip(missing, labels):
        if name not in seen:
            seen.add(name)
            unique_missing.append(name)
            unique_labels.append(label)

    return RequirementCheckResult(
        allowed=not unique_missing,
        missing=unique_missing,
        labels=unique_labels,
    )
