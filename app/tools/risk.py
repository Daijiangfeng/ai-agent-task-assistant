"""
工具风险分级（L0/L1/L2）与 HITL 审批策略。

三级风险模型：
- L0（低风险，只读）：calculator / web_search / 天气 / 公开信息查询 / 内部只读查询
  默认 AUTO（不触发人工审批）；
- L1（有业务影响）：创建记录 / 修改普通数据 / 发送通知
  默认按权限策略，必要时 HITL；
- L2（高风险/不可逆）：发邮件 / 创建订单 / 支付 / 删除数据 / 修改敏感数据
  默认 HITL。

风险判断可配置（settings.TOOL_APPROVAL_LEVEL），不硬编码：
- TOOL_APPROVAL_LEVEL="L2"（默认）：仅 L2 触发人工审批；
- TOOL_APPROVAL_LEVEL="L1"：L1 及以上触发人工审批（更保守）；
- TOOL_APPROVAL_LEVEL="L0"：所有工具都触发人工审批（最保守，等价旧行为）。
"""

from __future__ import annotations

from enum import Enum


class ToolRisk(str, Enum):
    """工具风险等级。"""

    L0 = "L0"  # 低风险只读，默认 AUTO
    L1 = "L1"  # 有业务影响，按策略/必要时 HITL
    L2 = "L2"  # 高风险不可逆，默认 HITL


# 工具名 -> 风险等级（未列出的工具按类别推断，缺省 L1）
TOOL_RISK_LEVELS: dict[str, ToolRisk] = {
    # ---- L0：只读、无副作用 ----
    "calculator": ToolRisk.L0,
    "web_search": ToolRisk.L0,
    "datetime_tool": ToolRisk.L0,
    "sql_query": ToolRisk.L0,  # 只读沙箱
    "http.get": ToolRisk.L0,
    "memory.get": ToolRisk.L0,
    "memory.search": ToolRisk.L0,
    "data.transform": ToolRisk.L0,
    "code.execute": ToolRisk.L0,  # 沙箱执行
    # ---- L1：有业务影响 ----
    "file_processing": ToolRisk.L1,
    "memory.set": ToolRisk.L1,
    "user.message": ToolRisk.L1,
    "user.ask": ToolRisk.L1,
    "database.write": ToolRisk.L1,
    "http.request": ToolRisk.L1,
    # ---- L2：高风险/不可逆 ----
    "email.send": ToolRisk.L2,
    "github.create_pr": ToolRisk.L2,
    "memory.delete": ToolRisk.L2,
    "user.approval": ToolRisk.L2,
}

DEFAULT_RISK = ToolRisk.L1

_RISK_RANK: dict[ToolRisk, int] = {ToolRisk.L0: 0, ToolRisk.L1: 1, ToolRisk.L2: 2}


def parse_risk_level(value: str | None) -> ToolRisk:
    """解析配置字符串为风险等级（非法值回退 L2，保持安全默认）。"""
    text = str(value or "").strip().upper()
    try:
        return ToolRisk(text)
    except ValueError:
        return ToolRisk.L2


def risk_rank(risk: ToolRisk) -> int:
    """风险等级数值（L0=0 < L1=1 < L2=2）。"""
    return _RISK_RANK.get(risk, 1)


def tool_risk_level(tool_name: str) -> ToolRisk:
    """获取工具风险等级（未声明时按类别推断，缺省 L1）。"""
    if tool_name in TOOL_RISK_LEVELS:
        return TOOL_RISK_LEVELS[tool_name]
    from app.tools.registry import ToolRegistry

    tool = ToolRegistry.get(tool_name)
    if tool is not None:
        try:
            schema = tool.to_schema()
        except Exception:  # pragma: no cover - 防御性
            schema = None
        if schema is not None:
            category = getattr(schema, "category", None)
            category_value = getattr(category, "value", str(category))
            if category_value in ("observe", "reason"):
                return ToolRisk.L0
            if category_value == "act":
                return ToolRisk.L2
    return DEFAULT_RISK


def requires_approval(tool_name: str, approval_level: str | None = None) -> bool:
    """
    判断某工具在当前审批级别下是否需要人工审批。

    Args:
        tool_name: 工具名。
        approval_level: 触发 HITL 的最低风险等级（"L0"/"L1"/"L2"）；
            None 时读取全局配置（settings.TOOL_APPROVAL_LEVEL）。

    Returns:
        是否需要人工审批。
    """
    if approval_level is None:
        from app.config.settings import get_settings

        approval_level = get_settings().TOOL_APPROVAL_LEVEL
    threshold = parse_risk_level(approval_level)
    return risk_rank(tool_risk_level(tool_name)) >= risk_rank(threshold)
