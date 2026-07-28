"""
RAG 系统单元测试。
覆盖文档加载、文本分块、索引与检索（mock embedding + 临时 Chroma）。
"""

import pytest

from app.config.settings import Settings
from app.rag.base import BaseReranker, Document
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


class FakeReranker(BaseReranker):
    """测试用重排器：按预设分数重排，或模拟失败。"""

    def __init__(self, scores: dict[str, float] | None = None, fail: bool = False):
        self._scores = scores or {}
        self._fail = fail
        self.calls = 0

    async def rerank(
        self, query: str, documents: list[Document], top_n: int
    ) -> list[Document]:
        self.calls += 1
        if self._fail:
            raise RuntimeError("rerank 服务不可用")
        out = []
        for doc in documents:
            metadata = dict(doc.metadata)
            metadata["rerank_score"] = self._scores.get(doc.content, 0.5)
            out.append(Document(content=doc.content, metadata=metadata))
        out.sort(key=lambda d: d.metadata["rerank_score"], reverse=True)
        return out[:top_n]


class TestRAGServiceRerank:
    """RAG rerank 精排流程测试（mock reranker，不触网）。"""

    async def _build_service(
        self,
        tmp_path,
        temp_chroma_dir,
        mock_embedding_provider,
        settings: Settings,
        reranker: BaseReranker | None,
    ) -> RAGService:
        """构建入库两条内容的 RAGService。"""
        f1 = tmp_path / "a.txt"
        f1.write_text("Python 异步编程使用 asyncio 事件循环。", encoding="utf-8")
        f2 = tmp_path / "b.txt"
        f2.write_text("Chroma 是一个向量数据库。", encoding="utf-8")

        service = RAGService(
            settings=settings,
            embedding_provider=mock_embedding_provider,
            vector_store=ChromaStore(temp_chroma_dir),
            reranker=reranker,
        )
        await service.ingest_file(str(f1))
        await service.ingest_file(str(f2))
        return service

    @pytest.mark.asyncio
    async def test_disabled_rerank_keeps_vector_results(
        self, tmp_path, temp_chroma_dir, mock_embedding_provider
    ):
        """开关关闭时行为不变：不调用 reranker，结果无 rerank_score。"""
        reranker = FakeReranker()
        settings = Settings(
            RAG_CHUNK_SIZE=100, RAG_CHUNK_OVERLAP=10, ENABLE_RERANK=False
        )
        service = await self._build_service(
            tmp_path, temp_chroma_dir, mock_embedding_provider, settings, reranker
        )
        hits = await service.search("异步编程", top_k=2)
        assert len(hits) >= 1
        assert reranker.calls == 0
        assert all("rerank_score" not in h for h in hits)

    @pytest.mark.asyncio
    async def test_rerank_reorders_by_score(
        self, tmp_path, temp_chroma_dir, mock_embedding_provider
    ):
        """开启时按 rerank 分数降序，结果携带 rerank_score。"""
        reranker = FakeReranker(
            scores={
                "Python 异步编程使用 asyncio 事件循环。": 0.2,
                "Chroma 是一个向量数据库。": 0.9,
            }
        )
        settings = Settings(
            RAG_CHUNK_SIZE=100, RAG_CHUNK_OVERLAP=10, ENABLE_RERANK=True
        )
        service = await self._build_service(
            tmp_path, temp_chroma_dir, mock_embedding_provider, settings, reranker
        )
        hits = await service.search("向量数据库", top_k=2)
        assert reranker.calls == 1
        assert len(hits) == 2
        assert hits[0]["content"] == "Chroma 是一个向量数据库。"
        assert hits[0]["rerank_score"] == 0.9

    @pytest.mark.asyncio
    async def test_rerank_score_threshold_filters(
        self, tmp_path, temp_chroma_dir, mock_embedding_provider
    ):
        """低于阈值的片段被过滤。"""
        reranker = FakeReranker(
            scores={
                "Python 异步编程使用 asyncio 事件循环。": 0.1,
                "Chroma 是一个向量数据库。": 0.8,
            }
        )
        settings = Settings(
            RAG_CHUNK_SIZE=100,
            RAG_CHUNK_OVERLAP=10,
            ENABLE_RERANK=True,
            RERANK_SCORE_THRESHOLD=0.5,
        )
        service = await self._build_service(
            tmp_path, temp_chroma_dir, mock_embedding_provider, settings, reranker
        )
        hits = await service.search("向量数据库", top_k=5)
        assert len(hits) == 1
        assert hits[0]["content"] == "Chroma 是一个向量数据库。"

    @pytest.mark.asyncio
    async def test_rerank_failure_falls_back_to_vector_order(
        self, tmp_path, temp_chroma_dir, mock_embedding_provider
    ):
        """rerank 失败时回退向量序结果，检索链路不中断。"""
        reranker = FakeReranker(fail=True)
        settings = Settings(
            RAG_CHUNK_SIZE=100, RAG_CHUNK_OVERLAP=10, ENABLE_RERANK=True
        )
        service = await self._build_service(
            tmp_path, temp_chroma_dir, mock_embedding_provider, settings, reranker
        )
        hits = await service.search("异步编程", top_k=2)
        assert reranker.calls == 1
        assert len(hits) >= 1
        assert all("rerank_score" not in h for h in hits)
