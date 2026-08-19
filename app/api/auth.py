"""
API 认证与授权依赖。

- 未启用认证（AUTH_ENABLED=false，开发模式）：放行，默认 admin 角色。
- 启用认证（生产模式）：校验 API Key（Authorization: Bearer 或 X-API-Key 头），
  无效或缺失返回 401；同时支持 X-User-Id / X-User-Role 头声明调用者身份与角色。

授权流程：Authentication -> UserContext(ToolContext) -> Resource Ownership Check
"""

from __future__ import annotations

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config.settings import get_settings
from app.tools.security import ROLE_ADMIN, ROLE_GUEST, ToolContext

_bearer = HTTPBearer(auto_error=False)


def can_access_task(task, user: ToolContext) -> bool:
    """
    资源所有权检查（Resource Ownership Check）。

    管理员可访问任意任务；其他角色仅能访问本人（owner_id 匹配）
    且同租户（tenant_id 匹配）的任务。
    """
    if user.role == ROLE_ADMIN:
        return True
    return task.owner_id == user.user_id and task.tenant_id == user.tenant_id


def _valid_keys() -> set[str]:
    """解析配置中的合法 API Key 集合。"""
    keys = get_settings().API_KEYS
    return {k.strip() for k in keys.split(",") if k.strip()}


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
    x_user_role: str | None = Header(default=None, alias="X-User-Role"),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
    x_trace_id: str | None = Header(default=None, alias="X-Trace-Id"),
) -> ToolContext:
    """
    解析并校验请求调用者身份。

    开发模式（AUTH_ENABLED=false）直接放行，角色取 X-User-Role 头或默认 admin；
    生产模式（AUTH_ENABLED=true）必须携带合法 API Key，否则 401。

    Returns:
        ToolContext：调用者身份（user_id / tenant_id / role / trace_id）。
    """
    settings = get_settings()

    if settings.AUTH_ENABLED:
        provided = None
        if credentials is not None and credentials.scheme.lower() == "bearer":
            provided = credentials.credentials
        elif x_api_key:
            provided = x_api_key
        if not provided or provided not in _valid_keys():
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="无效或缺失的 API Key",
                headers={"WWW-Authenticate": "Bearer"},
            )

    role = (x_user_role or "").strip().lower() or ROLE_ADMIN
    return ToolContext(
        user_id=(x_user_id or "anonymous").strip() or "anonymous",
        tenant_id=(x_tenant_id or "default").strip() or "default",
        role=role,
        trace_id=(x_trace_id or "").strip(),
    )


async def require_non_guest(
    user: ToolContext = Depends(get_current_user),
) -> ToolContext:
    """
    写操作授权：guest 角色禁止执行知识库写入/删除等变更操作。

    Args:
        user: 调用者身份（由 get_current_user 认证解析）。

    Returns:
        原样返回身份，供路由继续使用。

    Raises:
        HTTPException: guest 角色调用写操作时返回 403。
    """
    if user.role == ROLE_GUEST:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="guest 角色无权执行此操作，请使用 user/admin 角色",
        )
    return user
