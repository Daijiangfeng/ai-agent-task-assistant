"""
工具调用安全模型。

定义工具调用必须携带的身份上下文（ToolContext）、
角色-工具类别权限矩阵，以及各内置工具的类别归属。

权限矩阵（角色 x 工具类别）：
| 角色   | system | sql | file | network |
|--------|--------|-----|------|---------|
| guest  | 允许   | 否  | 否   | 否      |
| user   | 允许   | 允许| 允许 | 允许    |
| admin  | 允许   | 允许| 允许 | 允许    |

更细粒度的限制（如 user 角色 SQL 仅只读、文件仅限指定目录）
由工具内部实现保证：SQL 工具仅只读沙箱、文件工具仅限项目根目录。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ---- 工具类别 ----
CATEGORY_SYSTEM = "system"  # 无副作用基础工具（datetime/calculator）
CATEGORY_SQL = "sql"  # SQL 查询
CATEGORY_FILE = "file"  # 本地文件处理
CATEGORY_NETWORK = "network"  # 外部网络访问

# ---- 角色 ----
ROLE_GUEST = "guest"
ROLE_USER = "user"
ROLE_ADMIN = "admin"

_VALID_ROLES = frozenset({ROLE_GUEST, ROLE_USER, ROLE_ADMIN})

# 角色 -> 工具类别 -> 是否允许
ROLE_PERMISSIONS: dict[str, dict[str, bool]] = {
    ROLE_GUEST: {
        CATEGORY_SYSTEM: True,
        CATEGORY_SQL: False,
        CATEGORY_FILE: False,
        CATEGORY_NETWORK: False,
    },
    ROLE_USER: {
        CATEGORY_SYSTEM: True,
        CATEGORY_SQL: True,
        CATEGORY_FILE: True,
        CATEGORY_NETWORK: True,
    },
    ROLE_ADMIN: {
        CATEGORY_SYSTEM: True,
        CATEGORY_SQL: True,
        CATEGORY_FILE: True,
        CATEGORY_NETWORK: True,
    },
}

# 工具名 -> 类别（未列出的工具视为 CATEGORY_SYSTEM）
TOOL_CATEGORIES: dict[str, str] = {
    "datetime_tool": CATEGORY_SYSTEM,
    "calculator": CATEGORY_SYSTEM,
    "sql_query": CATEGORY_SQL,
    "file_processing": CATEGORY_FILE,
    "web_search": CATEGORY_NETWORK,
}


@dataclass(frozen=True)
class ToolContext:
    """
    工具调用身份上下文。

    工具执行必须携带身份：user_id 标识调用者、role 决定权限矩阵、
    tenant_id 用于多租户隔离、trace_id 用于全链路追踪。
    """

    user_id: str = "anonymous"
    tenant_id: str = "default"
    role: str = ROLE_GUEST
    trace_id: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "ToolContext":
        """从字典（如 AgentState）构造，缺失字段使用默认值。"""
        if not data:
            return cls()
        return cls(
            user_id=str(data.get("user_id", "anonymous")),
            tenant_id=str(data.get("tenant_id", "default")),
            role=str(data.get("role", ROLE_GUEST)),
            trace_id=str(data.get("trace_id", "")),
            extra=dict(data.get("extra", {}) or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典（写入 AgentState）。"""
        return {
            "user_id": self.user_id,
            "tenant_id": self.tenant_id,
            "role": self.role,
            "trace_id": self.trace_id,
            "extra": dict(self.extra),
        }


def is_role_allowed(role: str, category: str) -> bool:
    """
    按权限矩阵判断角色是否有权访问某工具类别。

    Args:
        role: 角色名（guest/user/admin）。
        category: 工具类别。

    Returns:
        是否允许。未知角色一律拒绝（fail-closed）。
    """
    role = role.lower() if role else ROLE_GUEST
    if role not in _VALID_ROLES:
        return False
    return ROLE_PERMISSIONS[role].get(category, False)
