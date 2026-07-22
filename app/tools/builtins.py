"""
内置工具集合。
提供开箱即用的基础工具实现。
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.config.logging import get_logger
from app.config.settings import get_settings
from app.tools.base import BaseTool, ToolInput, ToolOutput

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

    async def execute(self, input: ToolInput) -> ToolOutput:
        """
        执行日期时间查询。

        支持的 query 参数：
        - "now" / "" / 空: 返回完整日期时间
        - "date": 仅返回日期
        - "time": 仅返回时间
        - "timestamp": 返回 Unix 时间戳
        """
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
        return "执行数学计算。输入数学表达式（如 '2 + 3 * 4'），返回计算结果。"

    async def execute(self, input: ToolInput) -> ToolOutput:
        """
        执行数学表达式计算。

        支持：+, -, *, /, **, (), 基础数学函数。
        """
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
    - sql_query / file_processing / rag_retrieval: 无外部 Key 依赖，始终注册
    """
    from app.tools.file_processing import FileProcessingTool
    from app.tools.rag_tool import RAGRetrievalTool
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

    # RAG 知识库检索
    ToolRegistry.register(RAGRetrievalTool(settings))

    # Web 搜索：仅在配置了 Tavily API Key 时注册
    if settings.TAVILY_API_KEY:
        ToolRegistry.register(WebSearchTool(settings))
        logger.info("已注册 Web 搜索工具")
    else:
        logger.info("未配置 TAVILY_API_KEY，跳过 Web 搜索工具注册")
