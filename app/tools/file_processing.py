"""
文件处理工具。
复用 RAG 文档加载器解析本地文件，返回文本内容摘要，含路径安全校验。
"""

from __future__ import annotations

from pathlib import Path

from app.config.logging import get_logger
from app.config.settings import Settings, get_settings
from app.rag.loader import SUPPORTED_EXTENSIONS, DocumentLoader
from app.tools.base import BaseTool, ToolInput, ToolOutput

logger = get_logger(__name__)

# 内容摘要默认最大字符数
_DEFAULT_MAX_CHARS = 2000


class FileProcessingTool(BaseTool):
    """
    文件处理工具。

    解析本地文件（PDF/DOCX/TXT/MD）并返回文本内容（可截断）。
    出于安全考虑，仅允许读取项目根目录下的文件，防止路径越界。
    """

    def __init__(self, settings: Settings | None = None):
        self._settings = settings or get_settings()
        # 允许访问的根目录（项目根）
        from app.config.settings import BASE_DIR

        self._root = BASE_DIR.resolve()

    @property
    def name(self) -> str:
        return "file_processing"

    @property
    def description(self) -> str:
        return (
            "读取并解析本地文件内容（支持 PDF/DOCX/TXT/Markdown）。"
            "输入文件路径，返回文件的文本内容摘要。"
        )

    def _is_within_root(self, path: Path) -> bool:
        """校验路径是否在允许的根目录内。"""
        try:
            path.resolve().relative_to(self._root)
            return True
        except ValueError:
            return False

    async def execute(self, input: ToolInput) -> ToolOutput:
        """
        读取并解析文件内容。

        Args:
            input: query 为文件路径；parameters.max_chars 可控制截断长度。

        Returns:
            ToolOutput：成功时 data 为文本内容。
        """
        raw_path = input.query.strip()
        if not raw_path:
            return ToolOutput(success=False, error="文件路径为空")

        path = Path(raw_path)
        if not path.is_absolute():
            path = self._root / path

        if not self._is_within_root(path):
            return ToolOutput(success=False, error="禁止访问项目根目录之外的文件")

        if not path.exists():
            return ToolOutput(success=False, error=f"文件不存在: {raw_path}")

        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            return ToolOutput(
                success=False,
                error=(
                    f"不支持的文件类型: {path.suffix}。"
                    f"支持: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
                ),
            )

        try:
            documents = DocumentLoader.load(str(path))
            full_text = "\n\n".join(doc.content for doc in documents)

            max_chars = input.parameters.get("max_chars", _DEFAULT_MAX_CHARS)
            truncated = len(full_text) > max_chars
            content = full_text[:max_chars]

            return ToolOutput(
                success=True,
                data={
                    "source": str(path),
                    "content": content,
                    "truncated": truncated,
                    "total_chars": len(full_text),
                },
            )

        except Exception as e:
            logger.warning("文件处理失败", error=str(e), path=str(path))
            return ToolOutput(success=False, error=f"文件解析失败: {str(e)}")
