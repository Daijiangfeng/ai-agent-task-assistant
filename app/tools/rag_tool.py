"""
RAG 检索工具。
包装 RAGService.search()，让 Agent 能查询已索引的知识库。
"""

from __future__ import annotations

from app.config.logging import get_logger
from app.config.settings import Settings, get_settings
from app.rag.service import RAGService
from app.tools.base import BaseTool, ToolInput, ToolOutput

logger = get_logger(__name__)


class RAGRetrievalTool(BaseTool):
    """
    RAG 知识库检索工具。

    在已索引的文档知识库中做语义检索，返回最相关的文本片段，
    供 Agent 基于本地知识回答问题。
    """

    def __init__(
        self,
        settings: Settings | None = None,
        rag_service: RAGService | None = None,
    ):
        self._settings = settings or get_settings()
        self._service = rag_service

    def _get_service(self) -> RAGService:
        """惰性初始化 RAGService，避免启动时强依赖 embedding。"""
        if self._service is None:
            self._service = RAGService(self._settings)
        return self._service

    @property
    def name(self) -> str:
        return "rag_retrieval"

    @property
    def description(self) -> str:
        return (
            "在本地知识库中检索相关内容。输入问题或关键词，"
            "返回知识库中最相关的文档片段。适合回答基于已上传文档的问题。"
        )

    async def execute(self, input: ToolInput) -> ToolOutput:
        """
        执行知识库检索。

        Args:
            input: query 为查询文本；parameters.top_k 可控制返回数量。

        Returns:
            ToolOutput：成功时 data 为拼接的相关片段文本。
        """
        query = input.query.strip()
        if not query:
            return ToolOutput(success=False, error="查询内容为空")

        top_k = input.parameters.get("top_k")
        try:
            results = await self._get_service().search(query, top_k=top_k)
            if not results:
                return ToolOutput(success=True, data="知识库中未找到相关内容。")

            lines: list[str] = []
            for idx, item in enumerate(results, start=1):
                source = item.get("metadata", {}).get("source", "未知来源")
                content = item.get("content", "")
                lines.append(f"[{idx}] (来源: {source})\n{content}")
            return ToolOutput(success=True, data="\n\n".join(lines))

        except Exception as e:
            logger.warning("RAG 检索失败", error=str(e))
            return ToolOutput(success=False, error=f"检索失败: {str(e)}")
