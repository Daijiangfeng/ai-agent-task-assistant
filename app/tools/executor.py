"""
统一 Tool 执行管线（ToolExecutor）。

所有 Tool 调用统一经过：

    Resolve Tool -> Validate Input -> Check Permission -> Check Approval
        -> Create Execution Context -> Execute (timeout) -> Retry (幂等)
        -> Normalize Result -> Audit Log -> Return Result

设计取舍：
- 返回 ToolOutput（不抛出），便于 Agent 与 API 统一处理；
- 批准：request_approval=False 时假设上层（Agent 审批闸门）已处理；True 时可注入
  ApprovalGate 走审批；无 gate 且为 ACT 工具时返回 APPROVAL_REQUIRED。
- 重试：仅当异常 normalized 后 retryable=True 且工具幂等（is_idempotent）才重试；
  对 ACT 副作用工具默认不重试，避免重复 POST/发信/写库。
- 权限：角色矩阵 AND（若有显式 granted_permissions 则校验权限字符串），取更严格。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from app.config.logging import get_logger
from app.tools.base import ToolInput, ToolOutput
from app.tools.context import ToolExecutionContext
from app.tools.errors import (
    PermissionDeniedError,
    ToolExecutionError,
    ValidationError,
    normalize_exception,
)
from app.tools.permissions import granted_allows
from app.tools.schema import ToolCategory, ToolSchema
from app.tools.security import CATEGORY_SYSTEM, is_role_allowed

logger = get_logger(__name__)


def resolve_legacy_category(tool: Any) -> str:
    """
    解析工具用于旧角色×类别矩阵的类别。

    新工具同时声明 category（ToolCategory 枚举）与 legacy_category（旧字符串）；
    旧工具直接使用 category 字符串。用于 multi_agent 角色作用域与 _role_allows。
    """
    legacy = getattr(tool, "legacy_category", None)
    if legacy:
        return legacy
    category = getattr(tool, "category", CATEGORY_SYSTEM)
    if isinstance(category, ToolCategory):
        return getattr(tool, "legacy_category") or CATEGORY_SYSTEM
    return category or CATEGORY_SYSTEM


class ToolExecutor:
    """统一工具执行器（含解析/校验/权限/审批/超时/重试/规范化/审计）。"""

    DEFAULT_TIMEOUT = 30.0
    DEFAULT_BACKOFF_MS = 1000

    def __init__(
        self,
        registry=None,
        approval_gate=None,
        *,
        max_retries: int = 2,
        default_timeout: float | None = None,
    ):
        self._registry = registry
        self._approval_gate = approval_gate
        self._max_retries = max_retries
        self._default_timeout = default_timeout or self.DEFAULT_TIMEOUT

    # ------------------------------------------------------------------
    # 发现 + Schema
    # ------------------------------------------------------------------

    def get_registry(self):
        if self._registry is not None:
            return self._registry
        from app.tools.registry import ToolRegistry

        return ToolRegistry

    def list_schemas(
        self,
        granted_permissions: set[str] | None = None,
        category: ToolCategory | None = None,
    ) -> list[ToolSchema]:
        """列出 schemas，可选按权限过滤与按类别过滤。"""
        schemas = [t.to_schema() for t in self.get_registry().get_all().values()]
        if category is not None:
            schemas = [s for s in schemas if s.category is category]
        from app.tools.permissions import filter_by_permission

        return filter_by_permission(schemas, granted_permissions)

    def get_schema(self, name: str) -> ToolSchema | None:
        tool = self.get_registry().get(name)
        return tool.to_schema() if tool is not None else None

    # ------------------------------------------------------------------
    # 执行
    # ------------------------------------------------------------------

    async def execute(
        self,
        tool_name: str,
        params: dict[str, Any] | ToolInput,
        context: ToolExecutionContext | None = None,
        *,
        request_approval: bool = False,
        timeout: float | None = None,
    ) -> ToolOutput:
        ctx = context or ToolExecutionContext()
        tool = self.get_registry().get(tool_name)
        if tool is None:
            return ToolOutput(success=False, error=f"工具未注册: {tool_name}")
        if not tool.available:
            return ToolOutput(
                success=False,
                error=f"工具 {tool_name} 当前不可用（依赖基础设施未就绪）",
            )

        schema = tool.to_schema()
        exec_timeout = timeout if timeout is not None else (schema.timeout or self._default_timeout)
        t0 = time.perf_counter()

        # 1) 输入校验
        validation_error = self._validate(schema, params)
        if validation_error:
            raise ValidationError(validation_error)
        tool_input = self._to_tool_input(params)

        # 2) 权限校验
        allowed, reason = self._check_permission(tool, schema, ctx)
        if not allowed:
            raise PermissionDeniedError(reason, tool_name=tool_name)

        # 3) 审批（可选）
        if request_approval:
            approval = await self._request_approval(tool_name, tool_input, ctx, schema, params)
            if approval is not None:
                return approval

        # 4) 取消检查
        if ctx.cancelled:
            return ToolOutput(success=False, error="工具调用已取消")

        # 5) 执行 + 超时 + 幂等重试
        result = await self._execute_with_policy(tool, tool_input, ctx, schema, exec_timeout)

        # 6) 审计（记录放行与耗时）
        self._audit(ctx, tool_name, allowed=True, latency_ms=(time.perf_counter() - t0) * 1000)
        return result

    def _to_tool_input(self, params: dict[str, Any] | ToolInput) -> ToolInput:
        if isinstance(params, ToolInput):
            return params
        params = dict(params or {})
        # query/parameters 之外的顶层字段（来自工具 input_schema 的具名入参，
        # 如 http.get 的 url / code.execute 的 code）合并进 parameters，统一取参。
        parameters: dict[str, Any] = dict(params.get("parameters") or {})
        for key, value in params.items():
            if key in ("query", "parameters"):
                continue
            parameters[key] = value
        return ToolInput(
            query=str(params.get("query", "")),
            parameters=parameters,
        )

    def _validate(self, schema: ToolSchema, params: dict[str, Any] | ToolInput) -> str | None:
        """基于 input_schema 做轻量 required 校验（不引入额外依赖）。"""
        input_schema = schema.input_schema or {}
        required = input_schema.get("required") or []
        if not required:
            return None
        if isinstance(params, ToolInput):
            available = set()
            if params.query:
                available.add("query")
            available.update(params.parameters.keys())
        else:
            available = set((params or {}).keys())
        missing = [r for r in required if r not in available]
        if missing:
            return f"缺少必填参数: {', '.join(missing)}"
        return None

    def _check_permission(
        self, tool: Any, schema: ToolSchema, ctx: ToolExecutionContext
    ) -> tuple[bool, str | None]:
        """角色矩阵 AND 权限字符串（取更严格者）。"""
        cat = resolve_legacy_category(tool)
        if not is_role_allowed(ctx.role, cat):
            return False, f"当前角色 '{ctx.role}' 无权调用工具 {schema.name}"
        if ctx.granted_permissions is not None:
            required = schema.permissions
            if not granted_allows(required, ctx.granted_permissions):
                return False, (
                    f"缺少所需权限 {sorted(required)}（已授予: {sorted(ctx.granted_permissions)}）"
                )
        return True, None

    async def _request_approval(
        self,
        tool_name: str,
        tool_input: ToolInput,
        ctx: ToolExecutionContext,
        schema: ToolSchema,
        params: Any,
    ) -> ToolOutput | None:
        args = (
            params
            if isinstance(params, dict)
            else {"query": tool_input.query, "parameters": tool_input.parameters}
        )
        if self._approval_gate is None:
            if schema.category is ToolCategory.ACT:
                return ToolOutput(
                    success=False,
                    error="高风险操作需要人工审批，但未配置审批闸门",
                )
            return None
        outcome = await self._approval_gate.request(tool_name, dict(args), task_id=ctx.request_id)
        if outcome.decision != "approved":
            return ToolOutput(
                success=False,
                error=f"审批未通过: {outcome.reason or outcome.decision}",
            )
        return None

    async def _execute_with_policy(
        self,
        tool: Any,
        tool_input: ToolInput,
        ctx: ToolExecutionContext,
        schema: ToolSchema,
        timeout: float,
    ) -> ToolOutput:
        last_error: ToolExecutionError | None = None
        for attempt in range(self._max_retries + 1):
            if ctx.cancelled:
                return ToolOutput(success=False, error="工具调用已取消")
            coro = tool.execute(tool_input, context=ctx.tool_context)
            try:
                if ctx.cancelled:
                    return ToolOutput(success=False, error="工具调用已取消")
                result = await asyncio.wait_for(coro, timeout=timeout)
                # 工具成功或以 ToolOutput.error 形式返回失败：不做异常层重试
                return self._normalize(result)

            except asyncio.TimeoutError:
                return ToolOutput(success=False, error=f"工具执行超时（>{timeout}s）")
            except Exception as e:  # 收敛为统一错误
                normalized = normalize_exception(e, tool_name=schema.name)
                last_error = normalized
                # 仅当可重试、工具幂等、且未耗尽重试次数时才重试
                if not (normalized.retryable and schema.is_idempotent):
                    break
                if attempt >= self._max_retries:
                    break
                delay_ms = self.DEFAULT_BACKOFF_MS * (2 ** attempt)
                ctx.logger(schema.name).warning(
                    "ToolExecutor: 瞬时失败，准备重试",
                    attempt=attempt + 1,
                    delay_ms=delay_ms,
                    tool=schema.name,
                    code=normalized.code.value,
                )
                await asyncio.sleep(delay_ms / 1000.0)

        if last_error is not None:
            return ToolOutput(success=False, error=last_error.message)
        return ToolOutput(success=False, error="工具执行失败")

    def _normalize(self, result: ToolOutput) -> ToolOutput:
        """规范化工具返回值（保证为 ToolOutput 结构）。"""
        if isinstance(result, ToolOutput):
            return result
        return ToolOutput(success=True, data=result)

    def _audit(
        self, ctx: ToolExecutionContext, tool_name: str, allowed: bool, latency_ms: float
    ) -> None:
        """审计日志（结构化，避免记录敏感字段）。"""
        ctx.logger(tool_name).info(
            "ToolExecution",
            execution_id=ctx.execution_id,
            request_id=ctx.request_id,
            agent_id=ctx.agent_id,
            session_id=ctx.session_id,
            user_id=ctx.user_id,
            tenant_id=ctx.tenant_id,
            tool=tool_name,
            allowed=allowed,
            latency_ms=round(latency_ms, 1),
        )
        try:
            from app.tracing.recorder import get_trace_recorder

            get_trace_recorder().record_tool_call(
                task_id=ctx.request_id or ctx.session_id,
                tool=tool_name,
                allowed=allowed,
                latency_ms=latency_ms,
            )
        except Exception:  # pragma: no cover - 审计失败不影响执行
            pass
