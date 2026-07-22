"""
Agent 任务执行流程集成测试。

覆盖完整链路：AgentService.run_task -> LangGraph Workflow(Planner -> Executor -> Reflection)
-> 工具调用（web_search / sql_query / file_processing / rag_retrieval）-> 长期记忆保存与召回。

为保证离线可跑且确定性：
- 用一个实现了 Runnable 接口的 FakeChatModel 替代真实 LLM，
  根据 Prompt 文本区分 Planner / Executor / Reflection 调用，并驱动工具调用链。
- RAGService 使用真实实现 + mock embedding + 临时 Chroma（真实检索链路）。
- 长期记忆使用真实 VectorLongTermMemory + mock embedding + 临时 Chroma。
- Tavily 客户端被替换为假实现，避免真实网络请求。
"""

from __future__ import annotations

import json

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.runnables import Runnable

from app.config.settings import BASE_DIR, Settings
from app.memory.long_term import VectorLongTermMemory
from app.prompts.manager import PromptManager
from app.rag.service import RAGService
from app.rag.vector_store import ChromaStore
from app.services import agent_service as agent_service_module
from app.services.agent_service import AgentService
from app.services.task_service import TaskService
from app.tools.base import ToolInput
from app.tools.file_processing import FileProcessingTool
from app.tools.rag_tool import RAGRetrievalTool
from app.tools.registry import ToolRegistry
from app.tools.sql_query import SQLQueryTool
from app.tools.web_search import WebSearchTool

GOAL = "调研 Python 异步编程并结合本地知识库与数据给出总结"

# 计划固定为 4 个子任务，每个子任务描述中明确指向一个工具，
# FakeChatModel 会据此在 Executor 阶段发起对应的工具调用。
PLAN = {
    "goal": GOAL,
    "reasoning": "拆解为联网检索、数据库查询、本地文件读取与知识库检索四步。",
    "subtasks": [
        {
            "id": "task_1",
            "description": "使用 web_search 搜索 Python 异步编程的最新资料",
            "dependencies": [],
            "tool": "web_search",
        },
        {
            "id": "task_2",
            "description": "使用 sql_query 查询 employees 表统计部门人数",
            "dependencies": [],
            "tool": "sql_query",
        },
        {
            "id": "task_3",
            "description": "使用 file_processing 读取本地说明文件内容",
            "dependencies": [],
            "tool": "file_processing",
        },
        {
            "id": "task_4",
            "description": "使用 rag_retrieval 在本地知识库检索异步相关片段",
            "dependencies": [],
            "tool": "rag_retrieval",
        },
    ],
}

TOOL_NAMES = ("web_search", "sql_query", "file_processing", "rag_retrieval")

REFLECTION_JSON = {
    "is_satisfactory": True,
    "accuracy_score": 0.9,
    "completeness_score": 0.9,
    "relevance_score": 0.9,
    "issues": [],
    "suggestion": None,
}


def _messages_and_text(model_input):
    """将 Runnable 输入统一转换为消息列表与拼接文本。"""
    if hasattr(model_input, "to_messages"):
        messages = model_input.to_messages()
    elif isinstance(model_input, (list, tuple)):
        messages = list(model_input)
    else:
        messages = [model_input]

    parts = []
    for m in messages:
        content = getattr(m, "content", None)
        parts.append(content if isinstance(content, str) else str(content))
    return messages, "\n".join(parts)


class FakeChatModel(Runnable):
    """
    确定性假 ChatModel（实现 Runnable，可用于 prompt | llm | parser 链）。

    根据 Prompt 文本判定当前处于哪个节点：
    - 含 ToolMessage：Executor 第二轮，返回工具结果的综合回复
    - 含 "任务审查"：Reflection，返回满意的评估 JSON
    - 含 "任务规划"：Planner，返回固定计划 JSON（并记录注入的上下文）
    - 其余：Executor 第一轮，针对当前子任务发起一次工具调用
    """

    def __init__(self, plan: dict, record: dict, tools=None):
        self._plan = plan
        self._record = record
        self._tools = tools

    def bind_tools(self, tools, **kwargs) -> "FakeChatModel":
        return FakeChatModel(self._plan, self._record, tools)

    def invoke(self, model_input, config=None, **kwargs):  # pragma: no cover - 异步链路不会用到
        import asyncio

        return asyncio.run(self.ainvoke(model_input, config=config, **kwargs))

    async def ainvoke(self, model_input, config=None, **kwargs):
        messages, text = _messages_and_text(model_input)

        # Executor 第二轮：已带回工具执行结果，返回综合回复
        if any(isinstance(m, ToolMessage) for m in messages):
            self._record.setdefault("tool_result_rounds", 0)
            self._record["tool_result_rounds"] += 1
            return AIMessage(content="已根据工具返回结果完成该子任务。")

        # Reflection 节点
        if "任务审查" in text or "请评估上述执行结果" in text:
            return AIMessage(content=json.dumps(REFLECTION_JSON, ensure_ascii=False))

        # Planner / Replanner 节点
        if "任务规划" in text or "任务重新规划" in text or "请制定执行计划" in text:
            self._record.setdefault("planner_inputs", []).append(text)
            return AIMessage(content=json.dumps(self._plan, ensure_ascii=False))

        # Executor 第一轮：仅解析“当前子任务”段落，避免匹配到之前任务描述里的工具名
        tail = text.split("当前子任务")[-1] if "当前子任务" in text else text
        for name in TOOL_NAMES:
            if name in tail:
                return AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": name,
                            "args": {"query": f"integration:{name}"},
                            "id": f"call_{name}",
                            "type": "tool_call",
                        }
                    ],
                )

        # 未匹配到工具则直接返回文本结果
        return AIMessage(content="直接完成子任务，无需调用工具。")


class _FakeTavilyClient:
    """假 Tavily 客户端，返回固定结果，避免真实网络请求。"""

    def __init__(self, api_key=None):
        self.api_key = api_key

    def search(self, query, max_results=5):
        return {
            "results": [
                {
                    "title": "Async Python Guide",
                    "content": "asyncio 是 Python 的异步框架。",
                    "url": "https://example.com/async",
                }
            ]
        }


def _install_spy(tool):
    """为工具的 execute 方法安装调用记录代理。"""
    original = tool.execute
    calls: list[str] = []

    async def spy(input: ToolInput):
        calls.append(input.query)
        return await original(input)

    tool.execute = spy  # type: ignore[assignment]
    return calls


@pytest.fixture
def integration_env(tmp_path, temp_chroma_dir, mock_embedding_provider, monkeypatch):
    """
    构建完整集成测试环境：真实工具 + 真实 RAGService + 真实长期记忆。

    返回一个字典，包含 AgentService、TaskService、长期记忆、
    各工具的调用记录列表以及 FakeChatModel 的共享 record。
    """
    # 共享一个 Chroma 存储（RAG 与长期记忆用不同 collection）
    store = ChromaStore(temp_chroma_dir)

    rag_settings = Settings(RAG_CHUNK_SIZE=100, RAG_CHUNK_OVERLAP=10)

    # --- 真实 RAGService：入库一个临时知识文件 ---
    kb_file = tmp_path / "kb.txt"
    kb_file.write_text(
        "Python 异步编程通过 asyncio 事件循环实现并发。"
        "async/await 是核心语法。",
        encoding="utf-8",
    )
    rag_service = RAGService(
        settings=rag_settings,
        embedding_provider=mock_embedding_provider,
        vector_store=store,
    )

    # --- 真实长期记忆：Chroma + mock embedding ---
    long_term_memory = VectorLongTermMemory(
        settings=rag_settings,
        embedding_provider=mock_embedding_provider,
        vector_store=store,
    )

    # --- Tavily 假客户端 ---
    monkeypatch.setattr("tavily.TavilyClient", _FakeTavilyClient)

    # --- 本地文件（file_processing 要求在项目根目录内）---
    local_file = BASE_DIR / "_integration_tmp.txt"
    local_file.write_text("集成测试用本地说明文件。", encoding="utf-8")

    # --- 注册四个真实工具，并为 execute 安装调用记录代理 ---
    ToolRegistry.clear()
    web_tool = WebSearchTool(Settings(TAVILY_API_KEY="dummy", WEB_SEARCH_MAX_RESULTS=3))
    sql_tool = SQLQueryTool(Settings(SQLITE_SANDBOX_PATH=str(tmp_path / "sandbox.db")))
    file_tool = FileProcessingTool()
    rag_tool = RAGRetrievalTool(rag_service=rag_service)

    calls = {
        "web_search": _install_spy(web_tool),
        "sql_query": _install_spy(sql_tool),
        "file_processing": _install_spy(file_tool),
        "rag_retrieval": _install_spy(rag_tool),
    }

    for t in (web_tool, sql_tool, file_tool, rag_tool):
        ToolRegistry.register(t)

    # --- 修正 file_processing 子任务描述为真实存在的本地文件路径 ---
    plan = json.loads(json.dumps(PLAN))
    plan["subtasks"][2]["description"] = (
        f"使用 file_processing 读取本地文件 {local_file}"
    )

    # --- FakeChatModel 通过 record 共享状态 ---
    record: dict = {}

    class _FakeProvider:
        def get_chat_model(self):
            return FakeChatModel(plan, record)

        def get_client(self):  # pragma: no cover - 兼容接口
            return None

    class _FakeFactory:
        @staticmethod
        def create(*args, **kwargs):
            return _FakeProvider()

    monkeypatch.setattr(agent_service_module, "LLMProviderFactory", _FakeFactory)

    # Workflow 各节点依赖 PromptManager 中已注册的模板
    PromptManager.init_defaults()

    task_service = TaskService()
    agent_settings = Settings(ENABLE_LONG_TERM_MEMORY=True)
    service = AgentService(
        task_service=task_service,
        settings=agent_settings,
        long_term_memory=long_term_memory,
    )

    env = {
        "service": service,
        "task_service": task_service,
        "long_term_memory": long_term_memory,
        "calls": calls,
        "record": record,
        "local_file": local_file,
        "plan": plan,
    }
    try:
        yield env
    finally:
        local_file.unlink(missing_ok=True)
        ToolRegistry.clear()


class TestAgentIntegration:
    """完整 Agent 执行流程集成测试。"""

    @pytest.mark.asyncio
    async def test_full_flow_invokes_tool_chain(self, integration_env):
        """
        端到端执行：验证四类工具被 Planner 规划 + Executor 调用，
        任务成功产出最终结果。
        """
        service: AgentService = integration_env["service"]
        task_service: TaskService = integration_env["task_service"]
        calls = integration_env["calls"]

        task_id = await task_service.create_task(GOAL)
        final_result = await service.run_task(task_id, GOAL)

        # 任务成功完成
        assert final_result is not None
        assert GOAL in final_result

        # 四个工具都被真实调用过
        for name in TOOL_NAMES:
            assert len(calls[name]) >= 1, f"工具 {name} 未被调用"

        # 每个子任务的工具调用都完成了“工具结果综合回复”这一轮
        assert integration_env["record"].get("tool_result_rounds", 0) == 4

        # 任务状态被更新为完成
        task = await task_service.get_task(task_id)
        from app.models.task import TaskStatus

        assert task.status == TaskStatus.COMPLETED
        assert task.final_result == final_result

    @pytest.mark.asyncio
    async def test_long_term_memory_is_saved(self, integration_env):
        """任务完成后，结果摘要被写入长期记忆。"""
        service: AgentService = integration_env["service"]
        task_service: TaskService = integration_env["task_service"]
        memory: VectorLongTermMemory = integration_env["long_term_memory"]

        task_id = await task_service.create_task(GOAL)
        await service.run_task(task_id, GOAL)

        saved = await memory.get(f"task:{task_id}")
        assert saved is not None
        assert GOAL in saved

    @pytest.mark.asyncio
    async def test_long_term_memory_is_recalled(self, integration_env):
        """
        预置一条相关长期记忆后执行任务，验证其被检索并注入到 Planner 上下文。
        """
        service: AgentService = integration_env["service"]
        task_service: TaskService = integration_env["task_service"]
        memory: VectorLongTermMemory = integration_env["long_term_memory"]
        record = integration_env["record"]

        await memory.save("pref_style", "用户偏好：总结需简洁并附带代码示例")

        task_id = await task_service.create_task(GOAL)
        await service.run_task(task_id, GOAL)

        planner_inputs = record.get("planner_inputs", [])
        assert planner_inputs, "Planner 未被调用"
        # 召回的历史记忆应被拼接进 Planner 的上下文
        assert any("[相关历史记忆]" in text for text in planner_inputs)
        assert any("用户偏好" in text for text in planner_inputs)

    @pytest.mark.asyncio
    async def test_no_recall_when_memory_disabled(
        self, tmp_path, temp_chroma_dir, mock_embedding_provider, monkeypatch
    ):
        """长期记忆开关关闭时，不进行召回也不写入。"""
        ToolRegistry.clear()
        record: dict = {}

        class _FakeProvider:
            def get_chat_model(self):
                return FakeChatModel(json.loads(json.dumps(PLAN)), record)

            def get_client(self):
                return None

        class _FakeFactory:
            @staticmethod
            def create(*args, **kwargs):
                return _FakeProvider()

        monkeypatch.setattr("tavily.TavilyClient", _FakeTavilyClient)
        monkeypatch.setattr(agent_service_module, "LLMProviderFactory", _FakeFactory)
        PromptManager.init_defaults()

        # 无工具注册也应能跑通（Executor 无工具时直接调用 LLM）
        task_service = TaskService()
        service = AgentService(
            task_service=task_service,
            settings=Settings(ENABLE_LONG_TERM_MEMORY=False),
        )

        task_id = await task_service.create_task(GOAL)
        final_result = await service.run_task(task_id, GOAL)

        assert final_result is not None
        # Planner 上下文中不应出现召回标记
        planner_inputs = record.get("planner_inputs", [])
        assert planner_inputs
        assert all("[相关历史记忆]" not in text for text in planner_inputs)
        ToolRegistry.clear()
