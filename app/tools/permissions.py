"""
按工具"权限字符串"的授权模型（需求 §10）。

在既有"角色×类别矩阵"（app/tools/security.py）之上新增一层更细粒度的
permission-string 授权，例如：

    web.search        -> observe:web
    http.get          -> observe:http
    database.query    -> observe:database
    http.request      -> act:http
    email.send        -> act:email
    database.write    -> act:database
    memory.*          -> remember:*
    user.approval     -> interact:approval

模型要点：
- 需要权限列表与授予权限列表做匹配；授予支持 "observe:*" 通配与 "observe:http"
  前缀匹配；
- granted=None 表示"未显式限定"，由调用方决定是否信任角色矩阵（见 ToolExecutor）；
- 与角色矩阵双轨并存：ToolExecutor 的判定逻辑为"角色矩阵 AND（若有显式授权则
  校验权限字符串）"，取更严格者。
"""

from __future__ import annotations

from collections.abc import Iterable

WILDCARD = "*"


def split_permission(permission: str) -> tuple[str, str]:
    """将 'observe:web' 或 'observe:*' 拆为 (action, resource)。"""
    action, _, resource = str(permission).partition(":")
    return action.strip().lower(), resource.strip().lower()


def _match_grant(required: str, grant: str) -> bool:
    """单个 grant 是否覆盖 required（支持 action:* 与 action:resource 精确）。"""
    if grant == WILDCARD:
        return True
    r_action, r_resource = split_permission(required)
    g_action, g_resource = split_permission(grant)
    if g_action != r_action:
        return False
    if g_resource in (WILDCARD, ""):
        return True
    return g_resource == r_resource or r_resource.startswith(g_resource + ".")


def granted_allows(
    required: Iterable[str],
    granted: Iterable[str] | None,
) -> bool:
    """
    判断 required 权限集合是否被 granted 覆盖。

    - granted 为 None 视为未设限（由调用方决定是否信任角色矩阵）；
    - 集合匹配：required 中每一项只要被 granted 中任何一项覆盖即通过；
    - 空 required（工具无特殊权限）恒通过。
    """
    required = [r.strip().lower() for r in required if r and r.strip()]
    if not required:
        return True
    if granted is None:
        return True
    covered = [_match_grant(r, g.strip().lower()) for r in required for g in granted]
    return all(covered[i] for i in range(len(required))) and len(covered) >= len(required)


def filter_by_permission(
    schemas: Iterable,
    granted: Iterable[str] | None,
) -> list:
    """
    按授予权限过滤 tool schema 列表（Agent 用做"可用工具发现 + 权限过滤"）。

    granted=None 不过滤（信任角色矩阵）。
    """
    if granted is None:
        return list(schemas)
    granted_set = {g for g in granted if g}
    result = []
    for schema in schemas:
        required = getattr(schema, "permissions", None)
        if required is None or granted_allows(required, granted_set):
            result.append(schema)
    return result
