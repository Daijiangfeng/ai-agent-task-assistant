"""
统一 Tool 错误模型。

所有 Tool 不得把底层异常原样暴露给 Agent / 外部。通过本模块将异常转换为
带 code 的结构化错误，并区分是否可重试（retryable）。

错误码（需求 §14）：
    VALIDATION_ERROR / PERMISSION_DENIED / APPROVAL_REQUIRED / TIMEOUT_ERROR /
    RATE_LIMIT_ERROR / AUTHENTICATION_ERROR / EXTERNAL_SERVICE_ERROR /
    EXECUTION_ERROR / NOT_FOUND_ERROR

normalize_exception() 是唯一入口：未被识别的异常一律收敛为 EXECUTION_ERROR，
并剥离可能含敏感信息的底层异常细节（不泄露 password/api key/token）。
"""

from __future__ import annotations

import asyncio
from enum import Enum
from typing import Any


class ToolErrorCode(str, Enum):
    """结构化错误码。"""

    VALIDATION_ERROR = "VALIDATION_ERROR"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    TIMEOUT_ERROR = "TIMEOUT_ERROR"
    RATE_LIMIT_ERROR = "RATE_LIMIT_ERROR"
    AUTHENTICATION_ERROR = "AUTHENTICATION_ERROR"
    EXTERNAL_SERVICE_ERROR = "EXTERNAL_SERVICE_ERROR"
    EXECUTION_ERROR = "EXECUTION_ERROR"
    NOT_FOUND_ERROR = "NOT_FOUND_ERROR"


class ToolExecutionError(Exception):
    """统一 Tool 异常基类。"""

    code: ToolErrorCode = ToolErrorCode.EXECUTION_ERROR

    def __init__(
        self,
        message: str,
        *,
        retryable: bool = False,
        tool_name: str = "",
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.message = message
        self.retryable = retryable
        self.tool_name = tool_name
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        """转换为对外安全的结构化表示（不含底层异常细节）。"""
        return {
            "code": self.code.value,
            "retryable": self.retryable,
            "message": self.message,
        }


class ValidationError(ToolExecutionError):
    code = ToolErrorCode.VALIDATION_ERROR


class PermissionDeniedError(ToolExecutionError):
    code = ToolErrorCode.PERMISSION_DENIED


class ApprovalRequiredError(ToolExecutionError):
    code = ToolErrorCode.APPROVAL_REQUIRED


class ToolTimeoutError(ToolExecutionError):
    code = ToolErrorCode.TIMEOUT_ERROR


class RateLimitError(ToolExecutionError):
    code = ToolErrorCode.RATE_LIMIT_ERROR


class AuthenticationError(ToolExecutionError):
    code = ToolErrorCode.AUTHENTICATION_ERROR


class ExternalServiceError(ToolExecutionError):
    code = ToolErrorCode.EXTERNAL_SERVICE_ERROR


class ExecutionError(ToolExecutionError):
    code = ToolErrorCode.EXECUTION_ERROR


class NotFoundError(ToolExecutionError):
    code = ToolErrorCode.NOT_FOUND_ERROR


def normalize_exception(exc: BaseException, tool_name: str = "") -> ToolExecutionError:
    """
    将任意异常收敛为 ToolExecutionError。

    - 已是 ToolExecutionError 的直接返回；
    - asyncio.TimeoutError 映射为 ToolTimeoutError（可重试）；
    - 其余映射为 EXECUTION_ERROR（视网络/服务类关键字提示 retryable）。
    """
    if isinstance(exc, ToolExecutionError):
        return exc

    if isinstance(exc, asyncio.TimeoutError) or (
        isinstance(exc, TimeoutError) and not isinstance(exc, OSError)
    ):
        return ToolTimeoutError("工具执行超时", retryable=True, tool_name=tool_name)

    text = str(exc).lower()
    # 仅用于提示可重试性，不把底层细节暴露给外部（外部只看 message）
    transient_hints = (
        "timeout",
        "connection",
        "temporary",
        "unavailable",
        "429",
        "rate limit",
    )
    retryable = any(h in text for h in transient_hints)
    return ExecutionError(
        "工具执行失败",
        retryable=retryable,
        tool_name=tool_name,
    )
