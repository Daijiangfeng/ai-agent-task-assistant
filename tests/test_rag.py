"""
RAG 系统单元测试。
覆盖文档加载、文本分块、索引与检索（mock embedding + 临时 Chroma）。
"""

import pytest

from app.config.settings import Settings
from app.rag.indexer import ChromaIndexer
from app.rag.loader import DocumentLoader
from app.rag.retriever import ChromaRetriever
from app.rag.service import RAGService
from app.rag.splitter import TextSplitter
from app.rag.vector_store import ChromaStore


class TestDocumentLoader:
    """文档加载器测试。"""

    def test_load_txt(self, tmp_path):
        f = tmp_path / "a.txt"
        f.write_text("hello world", encoding="utf-8")
        docs = DocumentLoader.load(str(f))
        assert len(docs) == 1
        assert docs[0].content == "hello world"
        assert docs[0].metadata["source"] == str(f)

    def test_load_md(self, tmp_path):
        f = tmp_path / "b.md"
        f.write_text("# 标题\n正文内容", encoding="utf-8")
        docs = DocumentLoader.load(str(f))
        assert len(docs) == 1
        assert "正文内容" in docs[0].content

    def test_missing_file(self):
        with pytest.raises(FileNotFoundError):
            DocumentLoader.load("_not_exist_.txt")

    def test_unsupported_type(self, tmp_path):
        f = tmp_path / "c.xyz"
        f.write_text("data", encoding="utf-8")
        with pytest.raises(ValueError):
            DocumentLoader.load(str(f))


class TestTextSplitter:
    """文本分块器测试。"""

    def test_split_generates_chunks(self):
        from app.rag.base import Document

        settings = Settings(RAG_CHUNK_SIZE=100, RAG_CHUNK_OVERLAP=10)
        splitter = TextSplitter(settings)
        long_text = "。".join([f"这是第{i}个句子" for i in range(100)])
        docs = [Document(content=long_text, metadata={"source": "x.txt"})]
        chunks = splitter.split(docs)
        assert len(chunks) > 1
        for c in chunks:
            assert "chunk_id" in c.metadata
            assert "chunk_index" in c.metadata

    def test_chunk_ids_unique(self):
        from app.rag.base import Document

        settings = Settings(RAG_CHUNK_SIZE=100, RAG_CHUNK_OVERLAP=5)
        splitter = TextSplitter(settings)
        docs = [Document(content="a" * 500, metadata={"source": "y.txt"})]
        chunks = splitter.split(docs)
        ids = [c.metadata["chunk_id"] for c in chunks]
        assert len(ids) == len(set(ids))


class TestIndexerRetriever:
    """索引与检索测试（mock embedding + 临时 Chroma）。"""

    @pytest.mark.asyncio
    async def test_index_and_retrieve(
        self, tmp_path, temp_chroma_dir, mock_embedding_provider
    ):
        # 准备一个文本文件
        f = tmp_path / "doc.txt"
        f.write_text(
            "LangGraph 是一个用于构建有状态 Agent 的框架。"
            "它支持规划、执行和反思。",
            encoding="utf-8",
        )

        store = ChromaStore(temp_chroma_dir)
        splitter = TextSplitter(Settings(RAG_CHUNK_SIZE=100, RAG_CHUNK_OVERLAP=10))
        indexer = ChromaIndexer(mock_embedding_provider, store, splitter)
        chunk_ids = await indexer.index_file(str(f))
        assert len(chunk_ids) >= 1

        retriever = ChromaRetriever(mock_embedding_provider, store)
        results = await retriever.retrieve("LangGraph 框架", top_k=3)
        assert len(results) >= 1
        assert "score" in results[0].metadata

    @pytest.mark.asyncio
    async def test_delete_index(
        self, tmp_path, temp_chroma_dir, mock_embedding_provider
    ):
        f = tmp_path / "doc.txt"
        f.write_text("待删除的内容片段。", encoding="utf-8")

        store = ChromaStore(temp_chroma_dir)
        indexer = ChromaIndexer(mock_embedding_provider, store)
        chunk_ids = await indexer.index_file(str(f))
        await indexer.delete(chunk_ids)

        retriever = ChromaRetriever(mock_embedding_provider, store)
        results = await retriever.retrieve("待删除", top_k=3)
        assert results == []


class TestRAGService:
    """RAG 服务门面测试。"""

    @pytest.mark.asyncio
    async def test_ingest_and_search(
        self, tmp_path, temp_chroma_dir, mock_embedding_provider
    ):
        f = tmp_path / "kb.txt"
        f.write_text("向量检索让 Agent 能基于本地知识回答问题。", encoding="utf-8")

        store = ChromaStore(temp_chroma_dir)
        service = RAGService(
            settings=Settings(RAG_CHUNK_SIZE=100, RAG_CHUNK_OVERLAP=10),
            embedding_provider=mock_embedding_provider,
            vector_store=store,
        )
        result = await service.ingest_file(str(f))
        assert result["chunks_indexed"] >= 1

        hits = await service.search("向量检索", top_k=3)
        assert len(hits) >= 1
        assert "content" in hits[0]
