"""
文本分块器。
使用 LangChain RecursiveCharacterTextSplitter 将文档切分为带元数据的 chunk。

支持按文档类型动态选择分块参数（RAG_DYNAMIC_CHUNKING=true 时生效）：
- 代码类（.py/.js/.java/.go 等）：大块（1500/150），保持函数/类体上下文完整；
- 法律类（legal/contract 等）：小块（500/50），条款级粒度、定位精准；
- 其余类型使用默认 RAG_CHUNK_SIZE / RAG_CHUNK_OVERLAP。
"""

from __future__ import annotations

import hashlib

from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.config.settings import Settings, get_settings
from app.rag.base import Document

# 分块画像：类型键 -> (chunk_size, chunk_overlap)
CHUNK_PROFILES: dict[str, tuple[int, int]] = {
    "code": (1500, 150),  # 代码：上下文完整性优先
    "legal": (500, 50),  # 法律/合同：条款粒度，精确定位
    "table": (400, 50),  # 表格/数据：行粒度，避免跨行断裂
}

# 文件扩展名 -> 画像键
_EXTENSION_PROFILE: dict[str, str] = {
    ".py": "code", ".js": "code", ".ts": "code", ".tsx": "code",
    ".java": "code", ".go": "code", ".rs": "code", ".c": "code",
    ".cpp": "code", ".h": "code", ".hpp": "code", ".sql": "code",
    ".json": "code", ".yaml": "code", ".yml": "code",
}

# 文档 type（loader metadata）-> 画像键
_TYPE_PROFILE: dict[str, str] = {
    "code": "code",
    "legal": "legal",
    "contract": "legal",
    "regulation": "legal",
    "csv": "table",
    "excel": "table",
}


class TextSplitter:
    """
    文本分块器。

    将 Document 列表按配置的 chunk_size / chunk_overlap 切分，
    每个 chunk 继承父文档元数据并追加 chunk_id 与 chunk_index。
    动态分块开启时按文档类型（扩展名/type）选择画像参数。
    """

    def __init__(self, settings: Settings | None = None):
        self._settings = settings or get_settings()
        self._default = (
            self._settings.RAG_CHUNK_SIZE,
            self._settings.RAG_CHUNK_OVERLAP,
        )

    @classmethod
    def profile_for(cls, metadata: dict) -> str | None:
        """按元数据（type / source 扩展名）返回画像键，无匹配返回 None。"""
        meta_type = str(metadata.get("type") or "").lower()
        if meta_type in _TYPE_PROFILE:
            return _TYPE_PROFILE[meta_type]
        source = str(metadata.get("source") or "")
        from pathlib import Path

        return _EXTENSION_PROFILE.get(Path(source).suffix.lower())

    def _params_for(self, metadata: dict) -> tuple[int, int]:
        """当前文档的分块参数（动态分块关闭时恒为默认值）。"""
        if not self._settings.RAG_DYNAMIC_CHUNKING:
            return self._default
        profile = self.profile_for(metadata)
        if profile is None:
            return self._default
        return CHUNK_PROFILES.get(profile, self._default)

    def _splitter_for(self, metadata: dict) -> RecursiveCharacterTextSplitter:
        chunk_size, chunk_overlap = self._params_for(metadata)
        return RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            separators=["\n\n", "\n", "。", "！", "？", ".", " ", ""],
        )

    def split(self, documents: list[Document]) -> list[Document]:
        """
        将文档列表切分为 chunk（逐文档选择分块参数）。

        Args:
            documents: 待切分的文档列表。

        Returns:
            chunk 后的 Document 列表，每项含 chunk_id / chunk_index 元数据。
        """
        chunks: list[Document] = []
        for doc in documents:
            splitter = self._splitter_for(doc.metadata)
            pieces = splitter.split_text(doc.content)
            for idx, piece in enumerate(pieces):
                metadata = dict(doc.metadata)
                metadata["chunk_index"] = idx
                metadata["chunk_id"] = self._make_chunk_id(piece, metadata, idx)
                chunks.append(Document(content=piece, metadata=metadata))
        return chunks

    @staticmethod
    def _make_chunk_id(text: str, metadata: dict, idx: int) -> str:
        """基于来源、序号和内容哈希生成稳定的 chunk_id。"""
        source = metadata.get("source", "unknown")
        page = metadata.get("page", 0)
        digest = hashlib.md5(text.encode("utf-8")).hexdigest()[:8]
        return f"{source}::p{page}::c{idx}::{digest}"
