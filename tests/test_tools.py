"""
内置工具单元测试。
测试 DateTimeTool、CalculatorTool 和 ToolRegistry。
"""

import pytest

from app.config.settings import Settings
from app.tools.base import ToolInput
from app.tools.builtins import CalculatorTool, DateTimeTool, register_builtin_tools
from app.tools.file_processing import FileProcessingTool
from app.tools.registry import ToolRegistry
from app.tools.sql_query import SQLQueryTool
from app.tools.web_search import WebSearchTool


class TestDateTimeTool:
    """日期时间工具测试。"""

    def setup_method(self):
        self.tool = DateTimeTool()

    def test_tool_metadata(self):
        """测试工具名称和描述。"""
        assert self.tool.name == "datetime_tool"
        assert "日期" in self.tool.description or "时间" in self.tool.description

    @pytest.mark.asyncio
    async def test_execute_default(self):
        """测试默认查询返回完整日期时间。"""
        result = await self.tool.execute(ToolInput(query=""))
        assert result.success is True
        assert result.data is not None
        assert "UTC" in result.data

    @pytest.mark.asyncio
    async def test_execute_date(self):
        """测试查询日期。"""
        result = await self.tool.execute(ToolInput(query="date"))
        assert result.success is True
        # 格式 YYYY-MM-DD
        assert len(result.data) == 10

    @pytest.mark.asyncio
    async def test_execute_time(self):
        """测试查询时间。"""
        result = await self.tool.execute(ToolInput(query="time"))
        assert result.success is True
        assert ":" in result.data

    @pytest.mark.asyncio
    async def test_execute_timestamp(self):
        """测试查询时间戳。"""
        result = await self.tool.execute(ToolInput(query="timestamp"))
        assert result.success is True
        assert result.data.isdigit()

    @pytest.mark.asyncio
    async def test_to_langchain_tool(self):
        """测试转换为 LangChain Tool。"""
        lc_tool = self.tool.to_langchain_tool()
        assert lc_tool.name == "datetime_tool"


class TestCalculatorTool:
    """计算器工具测试。"""

    def setup_method(self):
        self.tool = CalculatorTool()

    def test_tool_metadata(self):
        """测试工具名称和描述。"""
        assert self.tool.name == "calculator"
        assert "计算" in self.tool.description or "数学" in self.tool.description

    @pytest.mark.asyncio
    async def test_addition(self):
        """测试加法。"""
        result = await self.tool.execute(ToolInput(query="2 + 3"))
        assert result.success is True
        assert result.data == "5"

    @pytest.mark.asyncio
    async def test_complex_expression(self):
        """测试复杂表达式。"""
        result = await self.tool.execute(ToolInput(query="(2 + 3) * 4"))
        assert result.success is True
        assert result.data == "20"

    @pytest.mark.asyncio
    async def test_division(self):
        """测试除法。"""
        result = await self.tool.execute(ToolInput(query="10 / 3"))
        assert result.success is True
        assert float(result.data) == pytest.approx(3.3333, rel=1e-2)

    @pytest.mark.asyncio
    async def test_division_by_zero(self):
        """测试除以零。"""
        result = await self.tool.execute(ToolInput(query="1 / 0"))
        assert result.success is False
        assert "零" in result.error

    @pytest.mark.asyncio
    async def test_empty_expression(self):
        """测试空表达式。"""
        result = await self.tool.execute(ToolInput(query=""))
        assert result.success is False

    @pytest.mark.asyncio
    async def test_invalid_characters(self):
        """测试包含非法字符。"""
        result = await self.tool.execute(ToolInput(query="import os"))
        assert result.success is False
        assert "不允许" in result.error


class TestRegisterBuiltinTools:
    """内置工具注册测试。"""

    def test_register_builtin_tools(self):
        """测试注册所有内置工具。"""
        ToolRegistry.clear()
        register_builtin_tools()

        all_tools = ToolRegistry.get_all()
        assert "datetime_tool" in all_tools
        assert "calculator" in all_tools
        # 无外部依赖的真实工具始终注册
        assert "sql_query" in all_tools
        assert "file_processing" in all_tools
        assert "rag_retrieval" in all_tools

    def test_tool_descriptions_not_empty(self):
        """测试工具描述不为空。"""
        register_builtin_tools()
        descriptions = ToolRegistry.get_tool_descriptions()
        assert "datetime_tool" in descriptions
        assert "calculator" in descriptions

    def test_langchain_tools_list(self):
        """测试获取 LangChain 工具列表。"""
        register_builtin_tools()
        lc_tools = ToolRegistry.get_all_langchain_tools()
        names = [t.name for t in lc_tools]
        # 至少包含 5 个无外部依赖工具
        assert len(lc_tools) >= 5
        assert "datetime_tool" in names
        assert "calculator" in names
        assert "sql_query" in names


class TestSQLQueryTool:
    """SQL 查询工具测试（SQLite 沙箱）。"""

    def _make_tool(self, tmp_path) -> SQLQueryTool:
        settings = Settings(SQLITE_SANDBOX_PATH=str(tmp_path / "sandbox.db"))
        return SQLQueryTool(settings)

    def test_metadata(self, tmp_path):
        tool = self._make_tool(tmp_path)
        assert tool.name == "sql_query"
        assert "SELECT" in tool.description or "查询" in tool.description

    @pytest.mark.asyncio
    async def test_select_sample_data(self, tmp_path):
        """正常 SELECT 查询示例数据。"""
        tool = self._make_tool(tmp_path)
        result = await tool.execute(ToolInput(query="SELECT * FROM employees"))
        assert result.success is True
        assert result.data["row_count"] == 5

    @pytest.mark.asyncio
    async def test_aggregate_query(self, tmp_path):
        """聚合查询。"""
        tool = self._make_tool(tmp_path)
        result = await tool.execute(
            ToolInput(query="SELECT department, COUNT(*) AS n FROM employees GROUP BY department")
        )
        assert result.success is True
        assert result.data["row_count"] >= 1

    @pytest.mark.asyncio
    async def test_reject_insert(self, tmp_path):
        """拒绝非 SELECT 语句。"""
        tool = self._make_tool(tmp_path)
        result = await tool.execute(
            ToolInput(query="INSERT INTO employees VALUES (9, 'x', 'y', 1, '2020-01-01')")
        )
        assert result.success is False

    @pytest.mark.asyncio
    async def test_reject_multi_statement(self, tmp_path):
        """拒绝多语句。"""
        tool = self._make_tool(tmp_path)
        result = await tool.execute(
            ToolInput(query="SELECT 1; DROP TABLE employees")
        )
        assert result.success is False

    @pytest.mark.asyncio
    async def test_reject_drop(self, tmp_path):
        """拒绝 DROP 关键字。"""
        tool = self._make_tool(tmp_path)
        result = await tool.execute(ToolInput(query="DROP TABLE employees"))
        assert result.success is False


class TestWebSearchTool:
    """Web 搜索工具测试。"""

    @pytest.mark.asyncio
    async def test_no_api_key(self):
        """未配置 API Key 时返回失败。"""
        tool = WebSearchTool(Settings(TAVILY_API_KEY=""))
        result = await tool.execute(ToolInput(query="latest AI news"))
        assert result.success is False
        assert "TAVILY_API_KEY" in result.error

    @pytest.mark.asyncio
    async def test_empty_query(self):
        """空查询返回失败。"""
        tool = WebSearchTool(Settings(TAVILY_API_KEY="dummy"))
        result = await tool.execute(ToolInput(query=""))
        assert result.success is False


class TestFileProcessingTool:
    """文件处理工具测试。"""

    @pytest.mark.asyncio
    async def test_read_text_file(self, tmp_path):
        """读取项目根目录内的临时文本文件。"""
        from app.config.settings import BASE_DIR

        f = BASE_DIR / "_test_tmp_file_processing.txt"
        f.write_text("hello file processing", encoding="utf-8")
        try:
            tool = FileProcessingTool()
            result = await tool.execute(ToolInput(query=str(f)))
            assert result.success is True
            assert "hello file processing" in result.data["content"]
        finally:
            f.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_reject_outside_root(self, tmp_path):
        """拒绝访问项目根目录之外的文件。"""
        outside = tmp_path / "outside.txt"
        outside.write_text("secret", encoding="utf-8")
        tool = FileProcessingTool()
        result = await tool.execute(ToolInput(query=str(outside)))
        assert result.success is False

    @pytest.mark.asyncio
    async def test_missing_file(self):
        """文件不存在返回失败。"""
        tool = FileProcessingTool()
        result = await tool.execute(ToolInput(query="_no_such_file_.txt"))
        assert result.success is False


class TestRAGRetrievalTool:
    """RAG 检索工具测试（mock RAGService）。"""

    @pytest.mark.asyncio
    async def test_retrieve_with_mock_service(self):
        """使用 mock RAGService 验证检索拼接。"""
        from unittest.mock import AsyncMock

        from app.tools.rag_tool import RAGRetrievalTool

        fake_service = AsyncMock()
        fake_service.search = AsyncMock(
            return_value=[
                {"content": "chunk A", "metadata": {"source": "doc.txt"}, "score": 0.9}
            ]
        )
        tool = RAGRetrievalTool(rag_service=fake_service)
        result = await tool.execute(ToolInput(query="question"))
        assert result.success is True
        assert "chunk A" in result.data

    @pytest.mark.asyncio
    async def test_empty_query(self):
        from app.tools.rag_tool import RAGRetrievalTool

        tool = RAGRetrievalTool()
        result = await tool.execute(ToolInput(query=""))
        assert result.success is False
