"""
文本分块器。
使用 LangChain RecursiveCharacterTextSplitter 将文档切分为带元数据的 chunk。
"""

from __future__ import annotations

import hashlib

from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.config.settings import Settings, get_settings
from app.rag.base import Document


class TextSplitter:
    """
    文本分块器。

    将 Document 列表按配置的 chunk_size / chunk_overlap 切分，
    每个 chunk 继承父文档元数据并追加 chunk_id 与 chunk_index。
    """

    def __init__(self, settings: Settings | None = None):
        self._settings = settings or get_settings()
        self._splitter = RecursiveCharacterTextSplitter(
            chunk_size=self._settings.RAG_CHUNK_SIZE,
            chunk_overlap=self._settings.RAG_CHUNK_OVERLAP,
            separators=["\n\n", "\n", "。", "！", "？", ".", " ", ""],
        )

    def split(self, documents: list[Document]) -> list[Document]:
        """
        将文档列表切分为 chunk。

        Args:
            documents: 待切分的文档列表。

        Returns:
            chunk 后的 Document 列表，每项含 chunk_id / chunk_index 元数据。
        """
        chunks: list[Document] = []
        for doc in documents:
            pieces = self._splitter.split_text(doc.content)
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
