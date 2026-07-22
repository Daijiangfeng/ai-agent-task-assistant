"""
Web 搜索工具。
基于 Tavily API 进行实时联网搜索，未配置 API Key 时返回明确错误。
"""

from __future__ import annotations

from app.config.logging import get_logger
from app.config.settings import Settings, get_settings
from app.tools.base import BaseTool, ToolInput, ToolOutput

logger = get_logger(__name__)


class WebSearchTool(BaseTool):
    """
    Web 搜索工具。

    使用 Tavily 搜索引擎返回与查询相关的实时结果（标题 + 摘要 + URL）。
    需要配置 TAVILY_API_KEY，否则返回失败结果。
    """

    def __init__(self, settings: Settings | None = None):
        self._settings = settings or get_settings()

    @property
    def name(self) -> str:
        return "web_search"

    @property
    def description(self) -> str:
        return (
            "联网搜索实时信息。输入搜索关键词或问题，返回相关网页的标题、"
            "摘要和链接。适合查询最新新闻、事实性信息等。"
        )

    async def execute(self, input: ToolInput) -> ToolOutput:
        """
        执行 Web 搜索。

        Args:
            input: query 为搜索关键词。

        Returns:
            ToolOutput：成功时 data 为拼接的结果文本。
        """
        query = input.query.strip()
        if not query:
            return ToolOutput(success=False, error="搜索关键词为空")

        api_key = self._settings.TAVILY_API_KEY
        if not api_key:
            return ToolOutput(success=False, error="未配置 TAVILY_API_KEY")

        try:
            from tavily import TavilyClient

            client = TavilyClient(api_key=api_key)
            max_results = self._settings.WEB_SEARCH_MAX_RESULTS
            response = client.search(query=query, max_results=max_results)
            results = response.get("results", []) if isinstance(response, dict) else []

            if not results:
                return ToolOutput(success=True, data="未找到相关结果。")

            lines: list[str] = []
            for idx, item in enumerate(results, start=1):
                title = item.get("title", "")
                content = item.get("content", "")
                url = item.get("url", "")
                lines.append(f"{idx}. {title}\n   {content}\n   来源: {url}")
            return ToolOutput(success=True, data="\n".join(lines))

        except Exception as e:  # pragma: no cover - 依赖网络/外部服务
            logger.warning("Web 搜索失败", error=str(e))
            return ToolOutput(success=False, error=f"搜索失败: {str(e)}")
