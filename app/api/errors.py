"""
全局异常处理和自定义错误响应。
提供统一的 API 错误格式和异常捕获机制。
"""

from __future__ import annotations

from fastapi import Request, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from app.config.logging import get_logger

logger = get_logger(__name__)


class ErrorHandlerMiddleware(BaseHTTPMiddleware):
    """
    全局异常处理中间件。

    捕获所有未处理的异常，返回统一格式的 JSON 错误响应。
    避免将内部错误细节暴露给客户端。
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        try:
            return await call_next(request)
        except Exception as exc:
            logger.error(
                "未处理的异常",
                path=request.url.path,
                method=request.method,
                error=str(exc),
                exc_info=True,
            )
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={
                    "detail": "服务器内部错误，请稍后重试。",
                    "path": request.url.path,
                },
            )


class AppException(Exception):
    """应用自定义异常基类。"""

    def __init__(
        self,
        message: str,
        status_code: int = status.HTTP_400_BAD_REQUEST,
        detail: str | None = None,
    ):
        self.message = message
        self.status_code = status_code
        self.detail = detail
        super().__init__(message)


class TaskNotFoundException(AppException):
    """任务不存在异常。"""

    def __init__(self, task_id: str):
        super().__init__(
            message=f"任务 {task_id} 不存在",
            status_code=status.HTTP_404_NOT_FOUND,
        )


class TaskStateException(AppException):
    """任务状态不允许当前操作异常。"""

    def __init__(self, task_id: str, current_status: str, allowed_statuses: list[str]):
        super().__init__(
            message=f"任务 {task_id} 当前状态为 {current_status}，"
            f"仅 {', '.join(allowed_statuses)} 状态可执行此操作",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
