"""
文件处理工具。
复用 RAG 文档加载器解析本地文件，返回文本内容摘要，含路径安全校验。

安全设计（纵深防御）：
- 路径限制：resolve() 后校验必须位于项目根目录内，防止 ../ 路径穿越与符号链接逃逸
- 敏感路径黑名单：禁止读取 .env / .git / venv / data 等敏感目录与密钥文件
- 文件大小限制：超过上限拒绝解析，防止大文件 DoS
- 内容魔数校验：PDF/DOCX 检查文件头，TXT/MD 校验 UTF-8 文本，防止伪装扩展名
"""

from __future__ import annotations

from pathlib import Path

from app.config.logging import get_logger
from app.config.settings import Settings, get_settings
from app.rag.loader import SUPPORTED_EXTENSIONS, DocumentLoader
from app.tools.base import BaseTool, ToolInput, ToolOutput
from app.tools.security import CATEGORY_FILE, ToolContext

logger = get_logger(__name__)

# 内容摘要默认最大字符数
_DEFAULT_MAX_CHARS = 2000
# 单文件最大大小（字节）
_MAX_FILE_SIZE = 10 * 1024 * 1024

# 禁止访问的路径片段（大小写不敏感，匹配任一路径组件即拒绝）
_FORBIDDEN_PATH_PARTS = frozenset(
    {
        ".env",  # 前缀匹配，覆盖 .env.local / .env.production 等
        ".git",
        "venv",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        "data",  # Chroma 向量库与 SQLite 沙箱目录
    }
)

# 禁止读取的敏感文件后缀（密钥/证书）
_FORBIDDEN_SUFFIXES = frozenset(
    {".pem", ".key", ".p12", ".pfx", ".crt", ".p8", ".jks", ".keystore"}
)


class FileProcessingTool(BaseTool):
    """
    文件处理工具。

    解析本地文件（PDF/DOCX/TXT/MD）并返回文本内容（可截断）。
    出于安全考虑，仅允许读取项目根目录下的文件，防止路径越界，
    并拒绝访问 .env / .git / 密钥等敏感文件。
    """

    def __init__(self, settings: Settings | None = None):
        self._settings = settings or get_settings()
        # 允许访问的根目录（项目根）
        from app.config.settings import BASE_DIR

        self._root = BASE_DIR.resolve()

    category: str = CATEGORY_FILE

    @property
    def name(self) -> str:
        return "file_processing"

    @property
    def description(self) -> str:
        return (
            "读取并解析本地文件内容（支持 PDF/DOCX/TXT/Markdown）。"
            "输入文件路径，返回文件的文本内容摘要。"
        )

    def _is_within_root(self, resolved: Path) -> bool:
        """校验已 resolve 的路径是否在允许的根目录内。"""
        try:
            resolved.relative_to(self._root)
            return True
        except ValueError:
            return False

    def _is_forbidden(self, resolved: Path) -> bool:
        """检查路径是否命中敏感路径黑名单。"""
        for part in resolved.parts:
            lowered = part.lower()
            if lowered in _FORBIDDEN_PATH_PARTS:
                return True
            if lowered.startswith(".env"):
                return True
        if resolved.suffix.lower() in _FORBIDDEN_SUFFIXES:
            return True
        return False

    def _check_file_signature(self, path: Path) -> str | None:
        """
        按扩展名校验文件内容魔数（浅 MIME 检查）。

        Returns:
            None 表示校验通过，否则返回错误消息。
        """
        suffix = path.suffix.lower()
        try:
            with path.open("rb") as f:
                head = f.read(512)
        except OSError as e:
            return f"文件读取失败: {e}"

        if suffix == ".pdf":
            if not head.startswith(b"%PDF"):
                return "文件内容与扩展名不符（缺少 PDF 文件头）"
        elif suffix == ".docx":
            if not head.startswith(b"PK\x03\x04"):
                return "文件内容与扩展名不符（缺少 DOCX 文件头）"
        else:  # txt / md / markdown
            if b"\x00" in head:
                return "文本文件包含二进制内容"
            try:
                head.decode("utf-8")
            except UnicodeDecodeError:
                return "文本文件不是有效的 UTF-8 编码"
        return None

    async def execute(
        self,
        input: ToolInput,
        context: ToolContext | None = None,
    ) -> ToolOutput:
        """
        读取并解析文件内容。

        Args:
            input: query 为文件路径；parameters.max_chars 可控制截断长度。
            context: 调用者身份上下文（权限矩阵校验）。

        Returns:
            ToolOutput：成功时 data 为文本内容。
        """
        auth_error = self._authorize(context)
        if auth_error:
            return ToolOutput(success=False, error=auth_error)

        raw_path = input.query.strip()
        if not raw_path:
            return ToolOutput(success=False, error="文件路径为空")

        path = Path(raw_path)
        if not path.is_absolute():
            path = self._root / path
        resolved = path.resolve()

        if not self._is_within_root(resolved):
            return ToolOutput(success=False, error="禁止访问项目根目录之外的文件")

        if self._is_forbidden(resolved):
            return ToolOutput(success=False, error="禁止访问敏感文件或目录")

        if not resolved.is_file():
            return ToolOutput(success=False, error=f"文件不存在: {raw_path}")

        try:
            size = resolved.stat().st_size
        except OSError:
            return ToolOutput(success=False, error=f"无法访问文件: {raw_path}")
        if size > _MAX_FILE_SIZE:
            return ToolOutput(
                success=False,
                error=f"文件过大（{size} 字节），超过上限 {_MAX_FILE_SIZE} 字节",
            )

        if resolved.suffix.lower() not in SUPPORTED_EXTENSIONS:
            return ToolOutput(
                success=False,
                error=(
                    f"不支持的文件类型: {resolved.suffix}。"
                    f"支持: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
                ),
            )

        signature_error = self._check_file_signature(resolved)
        if signature_error:
            return ToolOutput(success=False, error=signature_error)

        try:
            documents = DocumentLoader.load(str(resolved))
            full_text = "\n\n".join(doc.content for doc in documents)

            max_chars = input.parameters.get("max_chars", _DEFAULT_MAX_CHARS)
            truncated = len(full_text) > max_chars
            content = full_text[:max_chars]

            return ToolOutput(
                success=True,
                data={
                    "source": str(resolved),
                    "content": content,
                    "truncated": truncated,
                    "total_chars": len(full_text),
                },
            )

        except Exception as e:
            logger.warning("文件处理失败", error=str(e), path=str(resolved))
            return ToolOutput(success=False, error=f"文件解析失败: {str(e)}")
