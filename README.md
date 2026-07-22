# AI Agent Task Assistant -- 智能任务执行助手

企业级 LLM Agent 应用系统，具备任务规划、工具调用、知识增强、长期记忆和自我反思能力。

## 核心能力

- **Goal Understanding** -- 理解用户目标并拆解为可执行子任务
- **Task Planning** -- 基于 LangGraph 的智能任务规划
- **Tool Calling** -- 统一的工具调用框架
- **Memory Management** -- 短期记忆 (Redis，降级内存) + 长期记忆 (Chroma 向量库)
- **RAG Knowledge Retrieval** -- 文档解析 + 智谱 Embedding + Chroma 检索增强生成
- **Reflection Optimization** -- 执行结果自检与自动重新规划

## 技术栈

| 技术 | 用途 |
|------|------|
| Python 3.11+ | 开发语言 |
| FastAPI | Web API 框架 |
| LangGraph | Agent 状态机和工作流编排 |
| LangChain | LLM 调用链和工具集成 |
| 智谱 GLM | 大语言模型（OpenAI Compatible API） |
| 智谱 embedding-3 | 文本向量化（Memory / RAG 共用） |
| PostgreSQL | 持久化存储（任务、计划） |
| Redis | 会话缓存和短期记忆（连接失败自动降级内存） |
| Chroma | 向量数据库（RAG 检索 + 长期记忆，进程内持久化） |
| Tavily | 联网 Web 搜索（可选，需 API Key） |

## 架构说明

### 系统架构

```
Frontend/API
    |
    v
FastAPI Gateway
    |
    v
Agent Controller (AgentService)
    |
    v
LangGraph Workflow
    |
    +---> Planner Agent    (任务拆解与规划)
    |         |
    |         v
    +---> Executor Agent   (子任务执行 + 工具调用)
    |         |
    |         v
    +---> Reflection Agent (质量评估与反思)
              |
              +---> Replanner (不满意时重新规划)
```

### 模块说明

| 模块 | 路径 | 职责 |
|------|------|------|
| Agent | `app/agent/` | LangGraph 状态机、Planner/Executor/Reflection 节点 |
| LLM | `app/llm/` | LLM Provider 抽象层，智谱 GLM 接入 |
| Tools | `app/tools/` | 工具调用框架（抽象基类 + 注册表 + 内置工具） |
| Memory | `app/memory/` | 记忆系统：Redis 短期记忆（内存降级）+ Chroma 长期记忆 + 工厂 |
| RAG | `app/rag/` | RAG：文档加载/分块/向量化/索引/检索 + 服务门面 |
| Models | `app/models/` | Pydantic 数据模型 |
| Services | `app/services/` | 业务逻辑层（任务管理、Agent 执行） |
| API | `app/api/` | FastAPI 路由 + 全局异常处理 |
| Prompts | `app/prompts/` | Prompt 模板集中管理 |
| Config | `app/config/` | 配置管理、数据库连接、日志 |

### LangGraph 状态机流程

```
START --> [Planner] --> [Executor] --> [Reflection]
                          ^                |
                          |   (还有任务)    |
                          +----------------+
                          |                |
                          | (不满意+未超限)  |
                          +--[Replanner]---+
                                           |
                                       (完成/超限) --> END
```

**路由规则（Reflection 后）：**
- `should_replan=True` 且未超限 --> Replanner --> Executor
- 还有未完成任务 --> Executor (继续)
- 全部完成 --> END

### Memory / RAG 数据流

```
[Memory]
  短期记忆: save/get/delete/search --> Redis (stm:*) --(连接失败)--> InMemory 降级
  长期记忆: save/search --> 智谱 embedding-3 --> Chroma(collection=long_term_memory)
            AgentService 任务开始时 recall 注入 context，完成后 remember 写回
            （由 ENABLE_LONG_TERM_MEMORY 开关控制）

[RAG]
  ingest: 文件 --> DocumentLoader(PDF/DOCX/TXT/MD) --> TextSplitter(分块)
          --> 智谱 embedding-3 --> Chroma(collection=rag_documents)
  search: query --> embed --> Chroma 相似度检索(cosine) --> 相关片段
          RAGRetrievalTool / POST /knowledge/search 均复用此链路
```

### 内置工具

项目开箱即用提供以下内置工具，应用启动时自动注册到 `ToolRegistry`：

| 工具 | 名称 | 功能 |
|------|------|------|
| `DateTimeTool` | `datetime_tool` | 获取当前日期/时间/时间戳 |
| `CalculatorTool` | `calculator` | 执行数学表达式计算 |
| `WebSearchTool` | `web_search` | Tavily 联网搜索（仅当配置 `TAVILY_API_KEY` 时注册） |
| `SQLQueryTool` | `sql_query` | SQLite 沙箱只读查询（仅允许 SELECT，含示例数据） |
| `FileProcessingTool` | `file_processing` | 解析本地 PDF/DOCX/TXT/MD 文件内容（路径受限于项目根） |
| `RAGRetrievalTool` | `rag_retrieval` | 在已索引知识库中做语义检索 |

> Web 搜索工具依赖 `TAVILY_API_KEY`；未配置时自动跳过注册，其余工具无外部 Key 依赖，始终注册。

自定义工具只需继承 `BaseTool` 并实现 `name`、`description`、`execute()` 三个接口，然后注册到 `ToolRegistry`。

### 全局异常处理

API 层集成了 `ErrorHandlerMiddleware` 全局异常处理中间件：
- 捕获所有未处理的异常，返回统一 JSON 错误响应
- 支持自定义 `AppException` 异常体系（`TaskNotFoundException`、`TaskStateException` 等）
- 避免将内部错误细节暴露给客户端

---

## 运行说明

### 环境要求

- Python 3.11+
- PostgreSQL（可选，当前使用内存存储）
- Redis（可选，连接失败时短期记忆自动降级为进程内存）
- Chroma（进程内持久化，无需外部服务）
- Tavily API Key（可选，启用 Web 搜索工具时需要）

> 依赖说明：`chromadb`、`tavily-python`、`pypdf`、`python-docx` 为 Memory/RAG/工具新增依赖，
> 已列入 `requirements.txt`。`chromadb` 体积较大，首次安装耗时较长。向量库数据目录（`data/chroma`）
> 与 SQL 沙箱库（`data/sandbox.db`）建议加入 `.gitignore`。

### 安装与启动

```bash
# 1. 进入项目目录
cd ai-agent-task-assistant

# 2. 创建虚拟环境
python -m venv venv
source venv/bin/activate  # Linux/macOS
# venv\Scripts\activate   # Windows

# 3. 安装依赖
pip install -r requirements.txt

# 4. 配置环境变量
cp .env.example .env
# 编辑 .env 文件，填入智谱 API Key 等配置

# 5. 启动服务
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

### 访问

- **API 文档 (Swagger UI)**: http://localhost:8000/docs
- **API 文档 (ReDoc)**: http://localhost:8000/redoc
- **健康检查**: http://localhost:8000/health

---

## 示例请求

### 创建任务

```bash
curl -X POST http://localhost:8000/api/v1/tasks/ \
  -H "Content-Type: application/json" \
  -d '{"goal": "帮我分析最近一周的科技新闻趋势", "context": "关注 AI 领域"}'
```

**响应：**

```json
{
  "task_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "status": "pending",
  "plan": null,
  "created_at": "2026-07-21T10:30:00+00:00"
}
```

### 启动执行

```bash
curl -X POST http://localhost:8000/api/v1/tasks/{task_id}/execute
```

**响应：**

```json
{
  "task_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "status": "planning",
  "plan": null,
  "created_at": "2026-07-21T10:30:00+00:00"
}
```

### 查询状态

```bash
curl http://localhost:8000/api/v1/tasks/{task_id}
```

**响应：**

```json
{
  "task_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "status": "executing",
  "current_step": "执行子任务 2/3: 搜索 AI 领域新闻",
  "progress": 33.3,
  "plan": null,
  "final_result": null
}
```

### 列表查询

```bash
curl "http://localhost:8000/api/v1/tasks/?limit=10&offset=0"
```

**响应：**

```json
{
  "total": 3,
  "tasks": [
    {
      "task_id": "...",
      "status": "completed",
      "plan": null,
      "created_at": "2026-07-21T10:30:00+00:00"
    }
  ]
}
```

### 健康检查

```bash
curl http://localhost:8000/health
```

**响应：**

```json
{
  "status": "ok",
  "version": "0.1.0"
}
```

### 知识库入库（RAG）

将本地文件解析、分块、向量化并索引到 Chroma：

```bash
curl -X POST http://localhost:8000/api/v1/knowledge/documents \
  -H "Content-Type: application/json" \
  -d '{"file_path": "docs/handbook.md"}'
```

**响应：**

```json
{
  "source": "docs/handbook.md",
  "chunks_indexed": 12
}
```

### 知识库检索（RAG）

```bash
curl -X POST http://localhost:8000/api/v1/knowledge/search \
  -H "Content-Type: application/json" \
  -d '{"query": "如何配置向量库", "top_k": 3}'
```

**响应：**

```json
{
  "query": "如何配置向量库",
  "results": [
    {"content": "...", "metadata": {"source": "docs/handbook.md"}, "score": 0.82}
  ]
}
```

---

## 测试用例

### 运行测试

```bash
# 运行全部测试
pytest tests/ -v

# 运行指定模块
pytest tests/test_api.py -v
pytest tests/test_llm.py -v
pytest tests/test_agent.py -v
pytest tests/test_tools.py -v
pytest tests/test_memory.py -v
pytest tests/test_rag.py -v
```

> 测试全部离线可跑：智谱 embedding 用 mock provider，Chroma 用临时目录，Redis/Tavily 无需真实服务。

### 测试场景

| 测试文件 | 测试类/函数 | 覆盖场景 |
|----------|------------|---------|
| `test_llm.py` | `TestLLMProviderFactory::test_create_zhipu_provider` | 智谱 Provider 工厂创建 |
| `test_llm.py` | `TestLLMProviderFactory::test_create_invalid_provider` | 无效 Provider 异常处理 |
| `test_llm.py` | `TestLLMProviderFactory::test_register_custom_provider` | 自定义 Provider 注册 |
| `test_llm.py` | `TestZhipuProvider::test_get_chat_model` | ChatModel 实例创建 |
| `test_llm.py` | `TestZhipuProvider::test_get_chat_model_with_overrides` | 参数覆盖 |
| `test_llm.py` | `TestZhipuProvider::test_get_client` | OpenAI SDK Client 创建 |
| `test_agent.py` | `TestAgentState::test_state_creation` | AgentState 状态创建 |
| `test_agent.py` | `TestDataModels::test_subtask_creation` | SubTask 模型 |
| `test_agent.py` | `TestDataModels::test_plan_creation` | Plan 模型（含依赖） |
| `test_agent.py` | `TestDataModels::test_reflection_result` | ReflectionResult 模型 |
| `test_agent.py` | `TestDataModels::test_task_status_enum` | TaskStatus 枚举值 |
| `test_agent.py` | `TestToolRegistry::test_empty_registry` | 空工具注册表 |
| `test_api.py` | `TestHealthCheck::test_health_check` | 健康检查接口 |
| `test_api.py` | `TestTaskAPI::test_create_task` | 创建任务 |
| `test_api.py` | `TestTaskAPI::test_create_task_missing_goal` | 缺少字段校验 (422) |
| `test_api.py` | `TestTaskAPI::test_list_tasks` | 列表查询 |
| `test_api.py` | `TestTaskAPI::test_get_task_status` | 状态查询 |
| `test_api.py` | `TestTaskAPI::test_get_nonexistent_task` | 404 处理 |
| `test_api.py` | `TestTaskAPI::test_execute_task` | 启动任务执行（mock Agent） |
| `test_api.py` | `TestTaskAPI::test_execute_already_running_task` | 重复执行校验 (400) |
| `test_tools.py` | `TestDateTimeTool` | 日期时间工具（5 个场景） |
| `test_tools.py` | `TestCalculatorTool` | 计算器工具（7 个场景） |
| `test_tools.py` | `TestRegisterBuiltinTools` | 内置工具注册 |
| `test_tools.py` | `TestSQLQueryTool` | SQL 沙箱：拒绝非 SELECT/多语句/DROP、正常查询/聚合 |
| `test_tools.py` | `TestWebSearchTool` | 无 Key 返回失败、空查询校验 |
| `test_tools.py` | `TestFileProcessingTool` | 读取文件、拒绝越界路径、文件不存在 |
| `test_tools.py` | `TestRAGRetrievalTool` | mock RAGService 检索拼接、空查询 |
| `test_memory.py` | `TestInMemoryShortTermMemory` | 内存短期记忆 save/get/delete/ttl/search |
| `test_memory.py` | `TestRedisShortTermMemoryDegrade` | Redis 不可达时降级内存 |
| `test_memory.py` | `TestVectorLongTermMemory` | 长期记忆（mock embedding + 临时 Chroma）存取/检索 |
| `test_memory.py` | `TestMemoryFactory` | 记忆工厂创建 |
| `test_rag.py` | `TestDocumentLoader` | txt/md 解析、不存在/不支持类型 |
| `test_rag.py` | `TestTextSplitter` | 分块生成与 chunk_id 唯一性 |
| `test_rag.py` | `TestIndexerRetriever` | 索引/检索/删除（mock embedding + 临时 Chroma） |
| `test_rag.py` | `TestRAGService` | 服务门面 ingest + search |

---

## 项目结构

```
ai-agent-task-assistant/
├── app/
│   ├── agent/              # Agent Workflow (LangGraph 状态机)
│   │   ├── state.py        # 全局状态定义
│   │   ├── planner_node.py # Planner 节点
│   │   ├── executor_node.py# Executor 节点
│   │   ├── reflection_node.py # Reflection 节点
│   │   └── workflow.py     # 状态机构建
│   ├── api/                # FastAPI 路由
│   │   ├── v1/
│   │   │   ├── tasks.py    # 任务 CRUD API
│   │   │   ├── agent.py    # Agent 执行 API
│   │   │   └── knowledge.py# 知识库入库/检索 API
│   │   ├── router.py       # 路由汇总
│   │   ├── deps.py         # 依赖注入
│   │   └── errors.py       # 全局异常处理 |
│   ├── config/             # 配置管理
│   │   ├── settings.py     # Pydantic Settings
│   │   ├── database.py     # 数据库连接
│   │   └── logging.py      # 日志配置
│   ├── llm/                # LLM Provider
│   │   ├── base.py         # 抽象基类
│   │   ├── zhipu_provider.py # 智谱实现
│   │   ├── embeddings.py   # Embedding 抽象层 + 智谱 embedding-3
│   │   └── factory.py      # 工厂模式（LLM + Embedding）
│   ├── models/             # 数据模型
│   │   ├── task.py         # 任务模型
│   │   ├── plan.py         # 计划模型
│   │   └── api_schemas.py  # API Schema
│   ├── prompts/            # Prompt 模板
│   │   ├── manager.py      # Prompt 管理器
│   │   ├── planner.py      # Planner Prompt
│   │   ├── executor.py     # Executor Prompt
│   │   └── reflection.py   # Reflection Prompt
│   ├── tools/              # 工具框架
│   │   ├── base.py         # 工具抽象基类
│   │   ├── registry.py     # 工具注册表
│   │   ├── builtins.py     # 内置工具 + 注册入口
│   │   ├── web_search.py   # Tavily Web 搜索
│   │   ├── sql_query.py    # SQLite 沙箱只读查询
│   │   ├── file_processing.py # 本地文件解析
│   │   └── rag_tool.py     # RAG 知识库检索
│   ├── memory/             # 记忆系统
│   │   ├── base.py         # 抽象基类
│   │   ├── short_term.py   # Redis 短期记忆（内存降级）
│   │   ├── long_term.py    # Chroma 长期记忆
│   │   └── factory.py      # 记忆工厂
│   ├── rag/                # RAG 系统
│   │   ├── base.py         # 抽象基类 + Document
│   │   ├── loader.py       # 文档加载（PDF/DOCX/TXT/MD）
│   │   ├── splitter.py     # 文本分块
│   │   ├── vector_store.py # Chroma 封装
│   │   ├── indexer.py      # 索引器
│   │   ├── retriever.py    # 检索器
│   │   └── service.py      # RAG 服务门面
│   └── services/           # 业务服务层
│       ├── task_service.py # 任务管理
│       └── agent_service.py# Agent 执行（含长期记忆接入）
├── tests/                  # 测试
│   ├── conftest.py         # Fixtures + mock（含 mock embedding / 临时 Chroma）
│   ├── test_llm.py         # LLM 测试
│   ├── test_agent.py       # Agent 测试
│   ├── test_api.py         # API 测试
│   ├── test_tools.py       # 工具测试
│   ├── test_memory.py      # 记忆系统测试
│   └── test_rag.py         # RAG 测试
├── main.py                 # FastAPI 入口
├── pyproject.toml           # 项目配置 + pytest 配置
├── requirements.txt        # Python 依赖
├── .env.example            # 环境变量模板
└── README.md               # 项目文档
```

---

## 配置项

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `ANTHROPIC_AUTH_TOKEN` | (必填) | 智谱 GLM Anthropic 兼容端点 API Key（https://open.bigmodel.cn 申请） |
| `ANTHROPIC_BASE_URL` | `https://open.bigmodel.cn/api/anthropic` | 智谱 Anthropic 兼容端点地址 |
| `ZHIPU_MODEL` | `glm-4-plus` | 默认模型 |
| `ZHIPU_OPENAI_BASE_URL` | `https://open.bigmodel.cn/api/paas/v4/` | 智谱 OpenAI 兼容端点（仅用于 Embedding） |
| `ZHIPU_EMBEDDING_MODEL` | `embedding-3` | 智谱 Embedding 模型 |
| `TAVILY_API_KEY` | (可选) | Tavily 搜索 API Key，未填则不注册 Web 搜索工具 |
| `WEB_SEARCH_MAX_RESULTS` | `5` | Web 搜索返回结果数 |
| `CHROMA_PERSIST_DIR` | `data/chroma` | Chroma 持久化目录（留空用默认） |
| `RAG_CHUNK_SIZE` | `800` | RAG 分块大小 |
| `RAG_CHUNK_OVERLAP` | `100` | RAG 分块重叠 |
| `RAG_TOP_K` | `5` | RAG 检索默认返回数 |
| `SQLITE_SANDBOX_PATH` | `data/sandbox.db` | SQL 工具沙箱库路径（留空用默认） |
| `ENABLE_LONG_TERM_MEMORY` | `false` | 是否在任务中启用长期记忆召回/写入 |
| `REDIS_HOST` / `REDIS_PORT` / `REDIS_DB` | `localhost` / `6379` / `0` | Redis 连接（失败自动降级内存） |
| `MAX_REPLAN_ITERATIONS` | `3` | 最大重新规划次数 |
| `MAX_EXECUTION_STEPS` | `10` | 单任务最大执行步骤 |
| `DEBUG` | `false` | 调试模式 |
