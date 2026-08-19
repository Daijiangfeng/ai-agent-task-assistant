"""
内置工具集合。
提供开箱即用的基础工具实现。
"""


from __future__ import annotations

from datetime import datetime, timezone

from app.config.logging import get_logger
from app.config.settings import Settings, get_settings
from app.tools.base import BaseTool, ToolInput, ToolOutput
from app.tools.security import ToolContext

logger = get_logger(__name__)


class DateTimeTool(BaseTool):
    """
    日期时间工具。
    获取当前日期、时间、时区信息。
    """

    @property
    def name(self) -> str:
        return "datetime_tool"

    @property
    def description(self) -> str:
        return "获取当前日期和时间信息。支持查询当前时间、日期、时区等。"

    async def execute(
        self,
        input: ToolInput,
        context: ToolContext | None = None,
    ) -> ToolOutput:
        """
        执行日期时间查询。

        支持的 query 参数：
        - "now" / "" / 空: 返回完整日期时间
        - "date": 仅返回日期
        - "time": 仅返回时间
        - "timestamp": 返回 Unix 时间戳

        Args:
            input: 工具输入。
            context: 调用者身份上下文（权限矩阵校验）。
        """
        auth_error = self._authorize(context)
        if auth_error:
            return ToolOutput(success=False, error=auth_error)

        try:
            now = datetime.now(timezone.utc)
            query = input.query.strip().lower()

            if query in ("date", "日期"):
                result = now.strftime("%Y-%m-%d")
            elif query in ("time", "时间"):
                result = now.strftime("%H:%M:%S %Z")
            elif query in ("timestamp", "时间戳"):
                result = str(int(now.timestamp()))
            else:
                result = now.strftime("%Y-%m-%d %H:%M:%S %Z (%A)")

            return ToolOutput(success=True, data=result)

        except Exception as e:
            return ToolOutput(success=False, error=str(e))


class CalculatorTool(BaseTool):
    """
    计算器工具。
    支持基础数学表达式计算。
    """

    @property
    def name(self) -> str:
        return "calculator"

    @property
    def description(self) -> str:
        return "执行数学计算。输入数学表达式（如 \'2 + 3 * 4\'），返回计算结果。"

    async def execute(
        self,
        input: ToolInput,
        context: ToolContext | None = None,
    ) -> ToolOutput:
        """
        执行数学表达式计算。

        支持：+, -, *, /, **, (), 基础数学函数。

        Args:
            input: 工具输入。
            context: 调用者身份上下文（权限矩阵校验）。
        """
        auth_error = self._authorize(context)
        if auth_error:
            return ToolOutput(success=False, error=auth_error)

        try:
            expression = input.query.strip()
            if not expression:
                return ToolOutput(success=False, error="表达式为空")

            # 安全评估：仅允许数学运算
            allowed_chars = set("0123456789+-*/.() %")
            if not all(c in allowed_chars or c.isspace() for c in expression):
                return ToolOutput(
                    success=False,
                    error="表达式包含不允许的字符",
                )

            result = eval(expression, {"__builtins__": {}}, {})  # noqa: S307
            return ToolOutput(success=True, data=str(result))

        except ZeroDivisionError:
            return ToolOutput(success=False, error="除数不能为零")
        except Exception as e:
            return ToolOutput(success=False, error=f"计算错误: {str(e)}")


def register_builtin_tools() -> None:
    """
    注册所有内置工具到 ToolRegistry。

    基础工具（DateTime/Calculator）始终注册；
    真实工具按依赖/配置条件注册：
    - web_search: 仅当配置了 TAVILY_API_KEY
    - sql_query / file_processing: 无外部 Key 依赖，始终注册
    """
    from app.tools.file_processing import FileProcessingTool
    from app.tools.registry import ToolRegistry
    from app.tools.sql_query import SQLQueryTool
    from app.tools.web_search import WebSearchTool

    settings = get_settings()

    # 基础工具
    ToolRegistry.register(DateTimeTool())
    ToolRegistry.register(CalculatorTool())

    # SQL 查询（SQLite 沙箱，无外部依赖）
    ToolRegistry.register(SQLQueryTool(settings))

    # 文件处理（本地解析，无外部依赖）
    ToolRegistry.register(FileProcessingTool(settings))

    # Web 搜索：仅在配置了 Tavily API Key 时注册
    if settings.TAVILY_API_KEY:
        ToolRegistry.register(WebSearchTool(settings))
        logger.info("已注册 Web 搜索工具")
    else:
        logger.info("未配置 TAVILY_API_KEY，跳过 Web 搜索工具注册")

    # ---- 五类能力工具集（Observe/Reason/Act/Remember/Interact）----
    register_five_category_tools(settings)


def register_five_category_tools(settings: Settings | None = None) -> None:
    """
    注册面向统一 Tool Runtime 的五类能力工具（Observe/Reason/Act/Remember/Interact）。

    - 依赖基础设施的工具（http.request/github.create_pr）标记 unavailable，
      需接入端点/凭据后方可启用；email.send 使用内存通道（仅流程验证）。
    - Act 副作用工具在 Agent 调用时要求审批（ToolExecutor/审批闸门）。
    """
    settings = settings or get_settings()
    from app.tools.data_transform import DataTransformTool
    from app.tools.database_write import DatabaseWriteTool
    from app.tools.email_tool import EmailTool
    from app.tools.github_tool import GitHubCreatePRTool
    from app.tools.http_action import HTTPActionTool
    from app.tools.http_read import HTTPReadTool
    from app.tools.registry import ToolRegistry

    # Observe
    ToolRegistry.register(HTTPReadTool(settings))
    # Reason
    from app.tools.code_execution import CodeExecutionTool

    ToolRegistry.register(CodeExecutionTool())
    ToolRegistry.register(DataTransformTool())
    # Act
    ToolRegistry.register(HTTPActionTool(settings))
    if settings.is_production:
        # 生产禁止 Mock 工具降级：email.send 当前为内存通道（不真实外发），
        # 注册会让 Agent 误以为邮件已发送；接入真实 SMTP Provider 后方可启用
        logger.warning(
            "生产环境跳过 email.send 注册（当前实现为内存 Mock 通道）"
        )
    else:
        ToolRegistry.register(EmailTool())
    ToolRegistry.register(DatabaseWriteTool(settings))
    ToolRegistry.register(GitHubCreatePRTool())
    # Remember
    from app.tools.memory_tools import (
        MemoryDeleteTool,
        MemoryGetTool,
        MemorySearchTool,
        MemorySetTool,
    )

    for tool in (MemoryGetTool, MemorySetTool, MemorySearchTool, MemoryDeleteTool):
        ToolRegistry.register(tool(settings))
    # Interact
    from app.tools.interact_tools import UserApprovalTool, UserAskTool, UserMessageTool

    for tool in (UserMessageTool, UserAskTool, UserApprovalTool):
        ToolRegistry.register(tool())
    logger.info("已注册五类能力工具集（Observe/Reason/Act/Remember/Interact）")
