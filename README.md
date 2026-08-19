# AI Agent Task Assistant -- 智能任务执行助手

企业级 LLM Agent 全栈应用系统，具备任务规划、工具调用、知识增强、长期记忆和自我反思能力。
后端为 Python FastAPI + LangGraph Agent；前端为 React + Vite + TypeScript 单页应用，
落地 Apple 设计语言，覆盖系统概览、任务控制台、知识库管理与执行监控四大界面。

## 核心能力

- **Goal Understanding** -- 理解用户目标并拆解为可执行子任务
- **Task Planning** -- 基于 LangGraph 的智能任务规划
- **Tool Calling** -- 统一的工具调用框架（内置工具 + 五类能力工具集 + 高风险动作审批）
- **Required Parameter Check** -- 工具调用前参数完整性检查：缺失必填参数（如餐厅搜索缺位置）时确定性阻断，不依赖 Prompt 提示模型，改为向用户询问
- **Multi-Agent Collaboration** -- Supervisor 编排 Research/Data/Coding/Writing/Review 子 Agent，
  统一上下文（AgentContext）全链路透传（原始用户输入 / 已提取参数 / 中间结果 / 工具结果不丢失），
  Reviewer 基于完整信息合成最终交付
- **Human-in-the-loop 审批** -- 工具风险三级分级（L0 只读 AUTO / L1 有业务影响 / L2 高风险不可逆默认 HITL），
  高风险调用暂停执行，用户可批准 / 拒绝 / 修改参数后继续；风险阈值可配置（`TOOL_APPROVAL_LEVEL`）
- **任务生命周期控制** -- 暂停 / 恢复（断点续跑）/ 取消 / 重试（可从失败子任务恢复），基于 LangGraph Checkpoint
- **任务模板（Agent Skill）** -- 内置市场调研 / 文档分析 / 代码审查 / 通用模板，支持自定义模板与 `{var}` 变量渲染一键建任务
- **Memory Management** -- 短期记忆 (Redis，降级内存；模块已实现并通过测试，当前未接入业务链路) + 长期记忆 (Chroma 向量库，由 ENABLE_LONG_TERM_MEMORY 门控生效)
- **RAG Knowledge Retrieval** -- 文档解析 + 智谱 Embedding + Chroma 检索增强生成，支持重排与混合检索（可选，向量 + BM25 RRF）
- **Search Timeliness（搜索时效性）** -- 搜索意图分类（普通知识 / 时效性 / 新闻）+ 来源新鲜度评分 + 进程内缓存与强时效绕过，
  时效性问题优先采用最新检索结果，抑制模型用过时知识覆盖新结果
- **LLM 成本控制** -- 单任务预算（调用次数 / token / 金额）超限即终止，防止失控消耗
- **Reflection Optimization** -- 执行结果自检与自动重新规划
- **Execution Trace** -- 全链路执行追踪（任务 / 节点 / 工具调用 / Agent 步骤），记录输入、上下文快照、
  提取参数、审批结果、耗时、错误，供调试与审计
- **Retry / Timeout / Fallback** -- 工具瞬时失败有限重试（指数退避），子 Agent / Reviewer 超时保护，
  失败显式返回并透传给 Reviewer，避免把失败伪装成成功
- **Web UI** -- Apple 风格前端控制台：任务创建/进度轮询、生命周期控制与审批、知识库管理、任务模板、Agent 执行阶段可视化监控

## 技术栈

| 技术 | 用途 |
|------|------|
| Python 3.11+ | 开发语言 |
| FastAPI | Web API 框架 |
| LangGraph | Agent 状态机和工作流编排 |
| LangChain | LLM 调用链和工具集成 |
| 智谱 GLM | 大语言模型（Anthropic 兼容端点） |
| 智谱 embedding-3 | 文本向量化（Memory / RAG 共用） |
| PostgreSQL | 持久化存储（预留；当前任务为内存存储） |
| Redis | 会话缓存和短期记忆（连接失败自动降级内存；短期记忆模块已实现并通过测试，当前未接入业务链路，业务中仅长期记忆由 ENABLE_LONG_TERM_MEMORY 门控生效） |
| Chroma | 向量数据库（RAG 检索 + 长期记忆，进程内持久化） |
| Tavily | 联网 Web 搜索（可选，需 API Key） |
| React 18 + Vite + TypeScript | 前端单页应用（SPA） |
| react-router-dom | 前端路由（四大界面 + 路由级懒加载分包） |
| @tanstack/react-query | 数据请求与任务进度轮询（终态自动停轮询） |
| CSS 变量令牌 + CSS Modules | Apple 设计系统（零 UI 框架依赖） |
| Vitest + Testing Library | 前端单元/组件测试 |

## 架构说明

### 系统架构

```
React SPA (frontend/, Vite + TypeScript)
    |  REST  /api/v1  (开发期 Vite 代理 /api，生产 CORS 白名单)
    v
FastAPI Gateway
    |
    v
Agent Controller (AgentService)
    |
    v
LangGraph Workflow
    |
    +---> Supervisor Agent (执行模式决策 + 需求提取)
    |           |
    |           +-- multi_agent --> SubAgents (Research/Data/Coding/Writing)
    |           |                      |
    |           |                      +--> Reviewer (合成最终交付)
    |           |
    |           +-- single --> Planner Agent (任务拆解与规划)
    |                              |
    |                              v
    |                        Executor Agent (子任务执行 + 工具调用)
    |                              |
    |                              v
    |                        Reflection Agent (质量评估与反思)
    |                              |
    |                              +---> Replanner (不满意时重新规划)
```

### 模块说明

| 模块 | 路径 | 职责 |
|------|------|------|
| Agent | `app/agent/` | LangGraph 状态机、Planner/Executor/Reflection 节点、Supervisor/SubAgents/Reviewer 多 Agent 协作、统一上下文（context.py + AgentContext）、通用需求提取与参数完整性检查（requirements.py）、workflow 构建 |
| Queue | `app/queue/` | 任务队列抽象（Redis 可靠队列 / 内存队列，auto 自动降级）+ 内置 Worker（`python -m app.worker`） |
| LLM | `app/llm/` | LLM Provider 抽象层、智谱 GLM 接入、Embedding 工厂、单任务预算（成本控制） |
| Tools | `app/tools/` | 工具调用框架（抽象基类 + 注册表 + 内置工具）、统一执行管线（executor.py：校验/权限/审批/超时/重试/规范化/审计）、工具风险分级（risk.py）、搜索时效性（意图/新鲜度/缓存）、执行边界/审批钩子 |
| Memory | `app/memory/` | 记忆系统：Redis 短期记忆（内存降级）+ 长期记忆（向量库，按 user_id/tenant_id 隔离）+ 工厂 |
| RAG | `app/rag/` | RAG：文档加载/分块/向量化/索引/检索/重排 + 混合检索（BM25+向量 RRF）+ 可插拔向量库（chroma/pgvector） |
| Models | `app/models/` | Pydantic 数据模型（含任务模板）+ SQLAlchemy ORM（任务持久化） |
| Services | `app/services/` | 业务逻辑层（task_service 任务管理、task_control 暂停/取消控制、template_service 模板、agent_service Agent 执行 + 队列消费） |
| API | `app/api/` | FastAPI 路由（tasks/agent/knowledge/stats/tools/templates）+ 全局异常处理 + CORS |
| Prompts | `app/prompts/` | Prompt 模板集中管理（含多 Agent Supervisor/SubAgent/Reviewer 模板） |
| Tracing | `app/tracing/` | 执行追踪（任务 / 节点 span / 工具调用事件 / Agent 步骤事件，进程内环形缓存 + 结构化日志） |
| Config | `app/config/` | 配置管理、数据库连接、日志 |
| Frontend | `frontend/` | React + Vite + TS SPA（Apple 设计语言五大界面） |

### LangGraph 状态机流程

```
START --> [Supervisor] --multi_agent--> [SubAgents] --> [Reviewer] --> END
              |
              | single
              v
          [Planner] --> [Executor] --> [Reflection]
                           ^                |
                           |   (还有任务)    |
                           +----------------+
                           |                |
                           | (不满意+未超限)  |
                           +--[Replanner]---+
                                            |
                                        (完成/超限) --> END
```

**路由规则：**

- **Supervisor 后**：`execution_mode=multi_agent` 且分配了子 Agent --> SubAgents --> Reviewer --> END；
  否则进入单 Agent 流程（Planner）。
- **Reflection 后**：
  - `should_replan=True` 且未超限 --> Replanner --> Executor
  - 还有未完成任务 --> Executor (继续)
  - 全部完成 --> END

### Memory / RAG 数据流

```
[Memory]
  短期记忆: save/get/delete/search --> Redis (stm:*) --(连接失败)--> InMemory 降级
            （模块已实现并通过测试，但当前未接入业务链路）
  长期记忆: save/search --> 智谱 embedding-3 --> 向量库(collection=long_term_memory)
            ★ 数据隔离：每条记忆元数据携带 user_id/tenant_id/namespace，
              记录 ID 复合为 tenant:user:key，查询/读取/删除强制按作用域过滤，
              用户 A 的记忆不会被用户 B 召回（默认作用域 anonymous/default）
            AgentService 任务开始时 recall（限定调用者作用域）注入 context，
            完成后 remember 写回（由 ENABLE_LONG_TERM_MEMORY 开关控制）

[RAG]
  ingest: 文件 --> DocumentLoader(PDF/DOCX/TXT/MD) --> TextSplitter(分块)
          --> 智谱 embedding-3 --> 向量库(collection=rag_documents)
  search: query --> embed --> 向量召回(cosine, RETRIEVAL_TOP_K)
          --(ENABLE_RERANK=true)--> 智谱 Rerank 精排(阈值过滤) --> Top-K 相关片段
          --(关闭或 rerank 失败回退)--> 向量序 Top-K 相关片段
          RAGRetrievalTool / POST /knowledge/search 均复用此链路

[持久化]
  任务: TaskService --> 仓库层（memory | sqlite | PostgreSQL+SQLAlchemy Async，
        TASK_STORAGE_BACKEND=auto 时 PostgreSQL 优先、失败降级内存）
  执行检查点: LangGraph checkpointer（thread_id=task_id）--> PostgresSaver |
        MemorySaver（CHECKPOINT_BACKEND=auto 时 PostgreSQL 优先、失败降级内存）
```

### 内置工具

项目开箱即用提供以下内置工具，应用启动时自动注册到 `ToolRegistry`：

**核心工具（始终注册，`web_search` 按配置）：**

| 工具 | 名称 | 功能 |
|------|------|------|
| `DateTimeTool` | `datetime_tool` | 获取当前日期/时间/时间戳 |
| `CalculatorTool` | `calculator` | 执行数学表达式计算 |
| `WebSearchTool` | `web_search` | Tavily 联网搜索（仅当配置 `TAVILY_API_KEY` 时注册） |
| `SQLQueryTool` | `sql_query` | SQLite 沙箱只读查询（仅允许 SELECT，含示例数据） |
| `FileProcessingTool` | `file_processing` | 解析本地 PDF/DOCX/TXT/MD 文件内容（路径受限于项目根） |

**五类能力工具集（Observe/Reason/Act/Remember/Interact，统一 Tool Runtime）：**

| 类别 | 工具 | 说明 |
|------|------|------|
| Observe（观察） | `http.get`、`data.transform` | 只读 HTTP 抓取、数据转换；`http.get` 依赖端点就绪 |
| Reason（推理） | `code.execute` | 沙箱代码执行 |
| Act（行动） | `http.request`、`email.send`、`database.write`、`github.create_pr` | 有副作用操作；`email.send` 走内存通道，`github.create_pr` 依赖凭据 |
| Remember（记忆） | `memory.get`、`memory.set`、`memory.search`、`memory.delete` | 长期记忆读写 |
| Interact（交互） | `user.message`、`user.ask`、`user.approval` | 面向用户的通知/询问/审批 |

> `web_search` 依赖 `TAVILY_API_KEY`；未配置时自动跳过注册，其余工具无外部 Key 依赖，始终注册。
> 五类能力工具中依赖外部基础设施（HTTP 端点 / GitHub 凭据）的工具标记 `unavailable`，接入后启用。

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
- PostgreSQL（可选）：任务持久化（TASK_STORAGE_BACKEND=auto/postgres）、
  LangGraph Checkpoint（CHECKPOINT_BACKEND=auto/postgres）、pgvector 向量库后端均可用；
  未配置时任务/检查点自动降级为进程内存，适合开发。
- Redis（可选，连接失败时短期记忆自动降级为进程内存）
- Chroma（进程内持久化，无需外部服务；生产多实例部署建议切换 pgvector）
- Tavily API Key（可选，启用 Web 搜索工具时需要）

> 依赖说明：`chromadb`、`tavily-python`、`pypdf`、`python-docx` 为 Memory/RAG/工具新增依赖，
> 已列入 `requirements.txt`。`chromadb` 体积较大，首次安装耗时较长。向量库数据目录（`data/chroma`）、
> 任务 sqlite 库（`data/tasks.db`）与 SQL 沙箱库（`data/sandbox.db`）均已加入 `.gitignore`。

### 生产部署建议（多实例 / Kubernetes）

| 能力 | 开发/单机默认 | 生产推荐 | 配置 |
|------|--------------|----------|------|
| 任务存储 | 内存（重启丢失） | PostgreSQL（SQLAlchemy Async） | `TASK_STORAGE_BACKEND=auto` 或 `postgres` |
| 执行状态检查点 | MemorySaver（重启丢失） | PostgreSQL（PostgresSaver） | `CHECKPOINT_BACKEND=auto` 或 `postgres`（`ENABLE_CHECKPOINTING=true`） |
| 向量库 | Chroma 单机目录（多 Pod 数据竞争） | pgvector（Milvus/Qdrant 同理扩展） | `VECTOR_STORE_BACKEND=pgvector` + `EMBEDDING_DIM` |
| 长期记忆隔离 | 按 user_id + tenant_id 强过滤 | 同左（metadata 过滤 + 复合 ID） | 内置，无需配置 |

- 多租户隔离内置：任务按 `tenant_id` 隔离，长期记忆写入 `user_id`/`tenant_id` 元数据
  且查询强制过滤，用户 A 的记忆不会被用户 B 召回。
- 任务崩溃恢复：`thread_id=task_id` 的检查点 + 同线程重放，服务重启后重新提交执行
  即从断点继续，不重复执行已完成节点。

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

### 前端应用（Web UI）

前端位于 `frontend/`，使用 Vite + React + TypeScript。开发期通过 Vite 代理将 `/api`
转发到后端（`http://localhost:8000`），无需处理浏览器 CORS；生产由后端 `CORS_ORIGINS` 白名单放行。

```bash
# 1. 进入前端目录
cd frontend

# 2. 安装依赖
npm install

# 3. 启动开发服务器（默认 http://localhost:5173，自动代理 /api → :8000）
npm run dev

# 4. 生产构建 / 预览
npm run build
npm run preview

# 5. 类型检查与测试
npm run lint   # tsc --noEmit
npm run test   # vitest run
```

> 需先启动后端（`uvicorn main:app`）再启动前端，任务执行链路方可联调。
> 真实 Agent 执行依赖有效的智谱 API Key；无 Key 时前端会优雅展示 `failed`/空 plan 状态。

**五大界面：**

| 界面 | 路由 | 说明 |
|------|------|------|
| 系统概览仪表盘 | `/` | 消费 `GET /stats` + `/tools`：任务状态分布（10 态）、审批待办角标与列表、工具能力、知识库规模、健康/版本 |
| 任务控制台 | `/tasks`、`/tasks/:id` | 任务列表（待审批行高亮）、创建即执行表单、详情页：进度轮询/计划/子任务/反思/结果 + 暂停/恢复/取消/重试控制 + 待审批卡片（可改参批准）+ 多 Agent 结果 |
| 任务模板 | `/templates` | 内置 Agent Skill 与自定义模板卡片、`{var}` 变量输入一键创建任务（可立即执行）、模板 CRUD |
| 知识库管理 | `/knowledge` | 路径入库、文件上传、检索验证、已索引文档列表与删除 |
| Agent 执行监控 | `/monitoring`、`/monitoring/:id` | Planner→Executor→Reflection→Replanner 阶段时间线、迭代/版本、每子任务所用工具 |

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
  "plan": { "goal": "...", "subtasks": [], "version": 1, "reasoning": "..." },
  "subtasks": [
    {"id": "...", "description": "...", "status": "completed", "result": "...", "tool_used": "web_search", "error": null, "dependencies": []}
  ],
  "reflection": null,
  "iteration_count": 0,
  "plan_version": 1,
  "error": null,
  "final_result": null
}
```

> `plan`/`subtasks`/`reflection`/`iteration_count`/`plan_version`/`error` 为增强字段：
> Agent 执行时实时回写真实状态（不再恒为空），供任务详情页与执行监控消费。

### 列表查询

```bash
curl "http://localhost:8000/api/v1/tasks/?limit=10&offset=0"
# 按状态过滤（审批待办等）：status=pending/planning/executing/awaiting_approval/paused/cancelled/completed/failed ...
curl "http://localhost:8000/api/v1/tasks/?status=awaiting_approval"
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

### 任务生命周期控制

```bash
curl -X POST http://localhost:8000/api/v1/tasks/{task_id}/pause   # 暂停（节点边界生效）
curl -X POST http://localhost:8000/api/v1/tasks/{task_id}/resume  # 恢复（断点续跑）
curl -X POST http://localhost:8000/api/v1/tasks/{task_id}/cancel  # 取消
curl -X POST http://localhost:8000/api/v1/tasks/{task_id}/retry   # 重试（不传 from_index 从头执行）
curl -X POST http://localhost:8000/api/v1/tasks/{task_id}/retry \
  -H "Content-Type: application/json" -d '{"from_index": 2}'      # 从失败子任务恢复
```

### 高风险动作审批（Human-in-the-loop）

```bash
# 查看审批请求（含历史）
curl http://localhost:8000/api/v1/tasks/{task_id}/approvals

# 批准（可附带修改后的工具参数）
curl -X POST http://localhost:8000/api/v1/tasks/{task_id}/approvals/{approval_id}/approve \
  -H "Content-Type: application/json" \
  -d '{"note": "参数已核对", "modified_args": {"source": "docs/handbook.md"}}'

# 拒绝（工具不会执行，Agent 调整方案）
curl -X POST http://localhost:8000/api/v1/tasks/{task_id}/approvals/{approval_id}/reject \
  -H "Content-Type: application/json" -d '{"note": "该文档不可删除"}'
```

### 任务模板（Agent Skill）

```bash
# 列出模板（内置 market_research / document_analysis / code_review / general + 自定义）
curl http://localhost:8000/api/v1/templates/

# 基于模板创建任务（{var} 变量渲染；auto_execute=true 立即入队执行）
curl -X POST http://localhost:8000/api/v1/templates/market_research/run \
  -H "Content-Type: application/json" \
  -d '{"inputs": {"topic": "AI 行业", "aspect": "趋势", "language": "中文"}, "auto_execute": true}'

# 创建自定义模板（goal_template 支持 {var} 占位）
curl -X POST http://localhost:8000/api/v1/templates/ \
  -H "Content-Type: application/json" \
  -d '{"name": "竞品分析", "goal_template": "分析 {company} 的核心竞争力", "tags": ["调研"]}'
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

### 系统概览统计

供仪表盘一次性获取任务分布、工具数与知识库规模：

```bash
curl http://localhost:8000/api/v1/stats
```

**响应：**

```json
{
  "version": "0.1.0",
  "task_total": 3,
  "tasks_by_status": {"completed": 2, "executing": 1},
  "tool_count": 5,
  "knowledge_document_count": 2,
  "knowledge_chunk_count": 24
}
```

### 工具清单

列出已注册到 `ToolRegistry` 的内置工具：

```bash
curl http://localhost:8000/api/v1/tools
```

**响应：**

```json
{
  "total": 5,
  "tools": [
    {"name": "calculator", "description": "执行数学表达式计算"}
  ]
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

### 知识库文件上传

浏览器无法提供服务端路径，改用 multipart 上传（后端写临时文件后复用入库链路）：

```bash
curl -X POST http://localhost:8000/api/v1/knowledge/upload \
  -F "file=@docs/handbook.md"
```

### 已索引文档列表 / 删除

```bash
# 列出（按来源聚合，含分块数）
curl http://localhost:8000/api/v1/knowledge/documents

# 删除（source 作为查询参数，避免路径斜杠与路由冲突）
curl -X DELETE "http://localhost:8000/api/v1/knowledge/documents?source=docs/handbook.md"
```

---

## 架构改进

### 并行执行

- **并行子任务执行**：Executor 分析子任务的 `depends_on` 依赖关系，无依赖的子任务通过 `asyncio.gather()` 并行执行，显著减少多子任务场景的总时间
- Planner 生成计划时可包含 `depends_on: list[str]` 字段标注子任务间依赖
- 无 `depends_on` 字段时退化为原有逐个串行模式（向后兼容）

### 多 Agent 上下文一致性（AgentContext）

- **统一上下文视图**：`AgentContext`（`app/agent/context.py`）作为 `AgentState` 的读写视图，
  聚合 `original_user_query / conversation_history / extracted_requirements / missing_requirements /
  intermediate_results / tool_results / subagent_results`，沿 Supervisor → SubAgents → Reviewer
  全链路结构化透传，杜绝字符串拼接伪造上下文
- **信息不丢失**：Supervisor 创建任务时保留原始输入并回写确定性提取的参数；每个子 Agent 注入
  原始需求与已提取参数，不会再次声称"缺少目的地/时间/预算"；Reviewer 同时看到原始需求、
  所有子 Agent 产出与工具调用结果
- **结果合并安全**：列表字段使用 LangGraph `add` reducer 累加，避免串行/并行结果相互覆盖

### 工具调用可靠性

- **Required Parameter Check（参数完整性检查）**：工具可声明 `required_params`；调用前由
  `check_tool_requirements`（`app/agent/requirements.py`）合并「已提取参数 + query + 对话历史」
  做确定性校验，缺失时阻断工具调用并回填"向用户询问缺失参数"的说明，不依赖 Prompt 提示模型，
  不把 unknown/null/空字符串当有效参数，也不猜测/编造用户未提供的信息
- **工具风险分级（L0/L1/L2）**：`app/tools/risk.py` 按「只读 / 有业务影响 / 不可逆」三级划分，
  阈值可配置（`TOOL_APPROVAL_LEVEL`，默认 L2），`TOOL_APPROVAL_OVERRIDE_TOOLS` 可强制指定工具审批；
  只读、无副作用的 `web_search` 默认 L0 不触发 HITL
- **Retry / Timeout / Fallback**：工具瞬时失败有限重试（指数退避 1s→2s，最多 2 次，仅幂等工具）；
  子 Agent / Reviewer 的 LLM 调用有超时保护（`SUB_AGENT_TIMEOUT_SECONDS`）；失败显式返回错误状态，
  最终回答不把失败伪装成成功

### 搜索时效性（Search Timeliness）

- **搜索意图分类**：默认用确定性规则（关键词 + 时间词）把查询分为 `GENERAL_KNOWLEDGE /
  TIME_SENSITIVE / NEWS / CURRENT_STATUS / OFFICIAL_UPDATE / VERSION_RELEASE`，
  不同意图采用不同的 Tavily 请求参数与缓存策略（可选叠加 LLM 分类：`WEB_SEARCH_ENABLE_LLM_INTENT`）
- **来源新鲜度评分**：按发布时间（今天 / 1–3 天 / 4–7 天 / 8–30 天 / >30 天 / 未知）打分，
  结合来源质量（官方 / 可信 / 低质域名）排序；结果保留 URL、标题、发布日期、来源域等原始元数据，
  时效性问题优先采用最新检索结果，抑制模型用过时知识覆盖新结果
- **缓存与强时效绕过**：普通知识结果进程内缓存可配置（`WEB_SEARCH_CACHE_TTL`），
  强时效问题（今天/刚刚/当前）使用更短 TTL 并绕过缓存（`WEB_SEARCH_TIME_SENSITIVE_CACHE_TTL`）

### 检索增强（Hybrid + 动态分块）

- **混合检索（Hybrid Search）**：`ENABLE_HYBRID_SEARCH=true` 时，BM25 关键词召回与向量语义召回
  经 Reciprocal Rank Fusion 融合（`HYBRID_RRF_K`），提升专有名词/编号/型号命中；
  `rank_bm25` 缺失时自动降级为纯向量检索（不影响导入链）
- **动态分块（Dynamic Chunking）**：`RAG_DYNAMIC_CHUNKING=true` 时按文档类型选择分块参数
  （代码类大块、法律类小块），其余走 `RAG_CHUNK_SIZE` / `RAG_CHUNK_OVERLAP` 默认值

### 容错与恢复

- **工具调用重试**：对网络超时、连接错误等瞬时失败自动重试最多 2 次（指数退避 1s → 2s），非瞬时错误直接抛出
- **LangGraph 检查点持久化**：`workflow.build(checkpointer=...)` 支持传入 checkpointer 实例（如 `MemorySaver`、`SqliteSaver`），实现长时间任务的断点恢复

### 安全

- **SQL 沙箱增强**：`sqlparse` 双层检测（语句类型解析 + 关键字白名单），仅允许 SELECT/WITH 只读单语句
- **工具执行边界**：Executor 内置 `ToolExecutionPolicy`——仅允许已注册工具被调用，越权工具按角色×类别矩阵拒绝；副作用工具（如 `file_processing`）执行前经审批闸门，只读工具（`web_search` 等）默认放行
- **输入验证增强**：`goal` 最大 10000 字符、`context` 最大 50000 字符、`query` 最大 5000 字符，`top_k` 限制 1–50

### 可观测性

- **Executor 子任务延迟指标**：每个 task_result 自动记录 `latency_ms`，便于分析执行瓶颈
- **全链路执行追踪**：Trace 系统（`app/tracing/`）记录任务级 Run、节点 span（含 LLM 用量归因）、
  工具调用事件与 Agent 步骤事件（输入、上下文快照、提取参数、审批结果、耗时、错误），
  进程内环形缓存 + 结构化日志，可定位"每一步 context 是什么"，便于调试与审计
- **CI 安全扫描**：GitHub Actions 新增 `pip-audit` job 检测依赖漏洞，带 `--ignore-vuln PYSEC-2026-311` 豁免项（chromadb 相关漏洞：本项目未使用 trust_remote_code、内嵌使用未暴露服务端点、官方暂无修复版本，理由见 `.github/workflows/ci.yml` 注释）

### 可选依赖

- `sqlparse`（可选，未列入 `requirements.txt`；安装后增强 SQL 语句类型检测，未安装时自动回退纯正则校验）

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
pytest tests/test_integration.py -v
pytest tests/test_new_endpoints.py -v
pytest tests/test_multi_agent.py -v
pytest tests/test_approval.py -v
pytest tests/test_task_control.py -v
pytest tests/test_templates.py -v
pytest tests/test_checkpoint.py -v
pytest tests/test_agent_capabilities.py -v
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
| `test_memory.py` | `TestInMemoryShortTermMemory` | 内存短期记忆 save/get/delete/ttl/search |
| `test_memory.py` | `TestRedisShortTermMemoryDegrade` | Redis 不可达时降级内存 |
| `test_memory.py` | `TestVectorLongTermMemory` | 长期记忆（mock embedding + 临时 Chroma）存取/检索 |
| `test_memory.py` | `TestMemoryFactory` | 记忆工厂创建 |
| `test_integration.py` | `TestAgentIntegration` | Agent 端到端集成：FakeChatModel 驱动 Planner→Executor→Reflection 全流程 + 真实工具链（web_search/sql_query/file_processing，mock 外部依赖）+ 长期记忆回写 |
| `test_new_endpoints.py` | `TestStatsAndToolsAPI` | `/stats` 与 `/tools` 只读接口 |
| `test_new_endpoints.py` | `TestTaskStateWriteback` | Agent 执行中任务状态实时回写逻辑 |
| `test_multi_agent.py` | `TestSupervisorNode` / `TestSubAgentsNode` / `TestReviewerNode` | Supervisor 决策与回退、子 Agent 顺序执行/失败不阻断/前序结果传递、Reviewer 合成 |
| `test_approval.py` | `TestApprovalWorkflow` | HITL 审批端到端：interrupt 暂停 → 批准恢复完成 / 拒绝不执行 / 修改参数后执行 |
| `test_task_control.py` | `TestTaskControlService` / `TestLifecycleAPI` / `TestPlannerRetry` | 暂停/取消控制、pause/resume/cancel/retry API 全链路、from_index 断点重试 |
| `test_templates.py` | `TestAgentTemplateModel` / `TestTemplateService` / `TestTemplateAPI` | 模板变量渲染、内置种子与 CRUD 限制、模板 API 与 auto_execute 入队 |
| `test_checkpoint.py` | `TestCheckpoint` | LangGraph 检查点断点续跑（resume 不重跑已执行节点） |
| `test_agent_capabilities.py` | `TestCase6~10` | 多 Agent 上下文透传（Supervisor/SubAgent/Reviewer 拿到完整 Query 与已提取参数）、必填参数缺失阻断工具调用、web_search 默认不触发 HITL（风险分级可配置）、工具失败重试/回退、Reviewer 聚合上下文 |
| `test_budget.py` | `TestBudget` | 单任务 LLM 预算（次数/token/金额）超限终止 |
| `test_search_timeliness.py` | `TestSearchTimeliness` | 搜索意图分类、结果时效性评分、缓存绕过、来源质量排序 |
| `test_rag_hybrid.py` | `TestRagHybrid` | 混合检索（向量 + BM25 RRF 融合）与 BM25 缺失降级 |
| `test_tool_runtime.py` | `TestToolRuntime` | 统一 Tool Runtime（Schema/注册表/执行器/权限/错误处理） |
| `test_trace.py` | `TestTrace` | 执行追踪（Run/节点 span/工具调用事件） |
| `test_queue.py` | `TestQueue` | 任务队列抽象与异步 Worker |
| `test_task_storage.py` | `TestTaskStorage` | 任务存储后端（memory/sqlite） |
| `test_extract_text.py` | `TestExtractText` | LLM 响应 content 归一化（含 Anthropic block 列表） |

### 后端一键检查

```bash
python scripts/check.py   # ruff 代码检查 + pytest（应输出 ALL PASS）
```

### 前端测试

```bash
cd frontend
npm run lint   # tsc --noEmit 类型检查
npm run test   # vitest run
```

前端测试覆盖：`apiClient`（basePath/尾斜杠/错误抛出/网络异常）、`StatusPill` 10 态映射、
`CreateTaskForm` 提交校验、`taskRefetchInterval` 终态/等待人工介入停轮询。`ruff`/`pytest` 已在 `pyproject.toml` 中排除 `frontend/`。

---

## 项目结构

```
ai-agent-task-assistant/
├── app/
│   ├── main.py             # FastAPI 应用工厂 + lifespan + 中间件
│   ├── agent/              # Agent Workflow (LangGraph 状态机)
│   │   ├── state.py        # 全局状态定义（AgentState，含统一上下文字段）
│   │   ├── context.py      # 统一上下文视图（AgentContext，多 Agent 全链路透传）
│   │   ├── requirements.py # 通用需求提取 + 工具参数完整性检查（确定性阻断）
│   │   ├── planner_node.py # Planner 节点（含 replan 重规划）
│   │   ├── executor_node.py# Executor 节点（依赖分层并行 + 参数检查 + 重试 + 执行边界 + interrupt 审批透传）
│   │   ├── reflection_node.py # Reflection 节点
│   │   ├── multi_agent.py  # Supervisor / SubAgents / Reviewer 多 Agent 协作节点
│   │   ├── approval.py     # Human-in-the-loop 审批闸门（三级风险分级 + 可配置阈值）
│   │   ├── checkpoint.py   # LangGraph Checkpoint 工厂（PostgresSaver/MemorySaver 自动降级）
│   │   └── workflow.py     # 状态机构建（Supervisor 分支 + Planner 循环 + 可选 checkpointer）
│   ├── api/                # FastAPI 路由
│   │   ├── v1/
│   │   │   ├── tasks.py    # 任务创建/列表(状态过滤)/状态查询 + 暂停/恢复/取消/重试 + 审批 API
│   │   │   ├── agent.py    # 任务执行 API（POST /tasks/{id}/execute）
│   │   │   ├── knowledge.py# 知识库入库/上传/检索/列表/删除 API
│   │   │   ├── stats.py    # 系统概览统计 API
│   │   │   ├── templates.py# 任务模板 CRUD + 模板创建任务 API
│   │   │   └── tools.py    # 工具清单 API
│   │   ├── health.py       # 健康检查端点（/health，根路径）
│   │   ├── router.py       # 路由汇总（前缀 /api/v1）
│   │   ├── deps.py         # 依赖注入
│   │   └── errors.py       # 全局异常处理（AppException + 中间件）
│   ├── config/             # 配置管理
│   │   ├── settings.py     # Pydantic Settings（.env / 环境变量覆盖）
│   │   ├── database.py     # 数据库连接（PostgreSQL 预留）
│   │   └── logging.py      # structlog 日志配置
│   ├── llm/                # LLM Provider
│   │   ├── base.py         # 抽象基类
│   │   ├── zhipu_provider.py # 智谱实现（Anthropic 兼容端点）
│   │   ├── embeddings.py   # Embedding 抽象层 + 智谱 embedding-3
│   │   └── factory.py      # 工厂模式（LLM + Embedding）
│   ├── models/             # 数据模型
│   │   ├── task.py         # 任务模型（Task / SubTask / TaskStatus / ApprovalRequest）
│   │   ├── task_record.py  # SQLAlchemy ORM（任务持久化）
│   │   ├── plan.py         # 计划模型（Plan / ReflectionResult）
│   │   ├── template.py     # 任务模板模型（AgentTemplate，{var} 渲染）
│   │   └── api_schemas.py  # API Schema
│   ├── prompts/            # Prompt 模板
│   │   ├── manager.py      # Prompt 管理器
│   │   ├── planner.py      # Planner Prompt
│   │   ├── executor.py     # Executor Prompt
│   │   ├── reflection.py   # Reflection Prompt
│   │   └── multi_agent.py  # 多 Agent 模板（Supervisor/SubAgents/Reviewer）
│   ├── queue/              # 任务队列（异步化架构）
│   │   ├── base.py         # TaskQueue 抽象 + TaskMessage
│   │   ├── memory_queue.py # 进程内内存队列
│   │   ├── redis_queue.py  # Redis 可靠队列（不可达自动降级）
│   │   └── factory.py      # 队列工厂（auto/redis/memory）
│   ├── worker.py           # 内置 Worker 入口（python -m app.worker）
│   ├── tools/              # 工具框架
│   │   ├── base.py         # 工具抽象基类（BaseTool / ToolInput / ToolOutput / required_params）
│   │   ├── schema.py       # 统一 ToolSchema（类别/权限/执行模式/required_params）
│   │   ├── registry.py     # 工具注册表（类级单例）
│   │   ├── executor.py     # 统一执行管线（校验/权限/审批/超时/幂等重试/规范化/审计）
│   │   ├── security.py     # 工具执行边界（ToolExecutionPolicy + 审批钩子 + 角色权限）
│   │   ├── risk.py         # 工具风险分级（L0/L1/L2）与 HITL 判定
│   │   ├── permissions.py  # 权限字符串匹配
│   │   ├── errors.py       # 工具错误体系与归一化
│   │   ├── context.py      # 工具执行上下文（ToolContext / ToolExecutionContext）
│   │   ├── builtins.py     # 内置核心工具（datetime/calculator/web_search/sql/file/rag）+ 注册入口
│   │   ├── web_search.py   # Tavily Web 搜索（含意图分类/时效性/缓存）
│   │   ├── sql_query.py    # SQLite 沙箱只读查询
│   │   ├── file_processing.py # 本地文件解析
│   │   ├── rag_tool.py     # RAG 知识库检索
│   │   ├── search_intent.py / search_freshness.py / search_cache.py # 搜索意图/时效性/缓存
│   │   ├── http_read.py / http_action.py # HTTP 只读/行动工具
│   │   ├── code_execution.py / data_transform.py # Reason 类工具
│   │   ├── database_write.py / email_tool.py / github_tool.py # Act 类工具
│   │   ├── memory_tools.py # Remember 类工具（get/set/search/delete）
│   │   └── interact_tools.py # Interact 类工具（message/ask/approval）
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
│   │   ├── hybrid_retriever.py # 混合检索（向量 + 关键词 BM25）
│   │   ├── indexer.py      # 索引器
│   │   ├── retriever.py    # 检索器
│   │   ├── reranker.py     # 智谱 Rerank 精排（ENABLE_RERANK 开启时生效）
│   │   └── service.py      # RAG 服务门面
│   ├── tracing/            # 执行追踪（recorder.py：Run/NodeSpan/ToolCallEvent/AgentStepEvent + 环形缓存）
│   └── services/           # 业务服务层
│       ├── task_service.py # 任务管理（内存/SQLite/PostgreSQL 可插拔仓库）
│       ├── task_repository.py # 任务仓库层
│       ├── task_control.py # 暂停/取消控制注册表
│       ├── template_service.py # 任务模板服务（内置种子 + CRUD + 渲染建任务）
│       └── agent_service.py# Agent 执行（队列消费 + Checkpoint + 长期记忆）
├── frontend/               # React + Vite + TS 前端 SPA
│   ├── src/
│   │   ├── main.tsx        # 入口（Provider 链：Query/Toast/Router/ErrorBoundary）
│   │   ├── App.tsx         # 路由配置（React.lazy 分包五大界面）
│   │   ├── lib/            # types / apiClient / queryClient / cx
│   │   ├── styles/         # tokens.css（Apple 设计令牌）+ globals.css
│   │   ├── components/     # 原语组件库（Button/StatusPill/Table/...）
│   │   └── features/       # dashboard / tasks / templates / knowledge / monitoring
│   ├── index.html
│   ├── vite.config.ts      # /api 代理 + manualChunks 分包
│   ├── vitest.setup.ts     # 前端测试环境初始化
│   ├── tsconfig.json
│   └── package.json
├── tests/                  # 后端测试（全部离线可跑）
│   ├── conftest.py         # Fixtures + mock（含 mock embedding / 临时 Chroma / 队列隔离）
│   ├── test_llm.py         # LLM 测试
│   ├── test_agent.py       # Agent 测试
│   ├── test_api.py         # API 测试
│   ├── test_tools.py       # 工具测试
│   ├── test_memory.py      # 记忆系统测试
│   ├── test_rag.py         # RAG 测试
│   ├── test_integration.py # Agent 端到端集成测试（FakeChatModel + 真实工具链）
│   ├── test_new_endpoints.py # stats/tools 接口 + 任务状态回写测试
│   ├── test_multi_agent.py # 多 Agent 协作测试
│   ├── test_approval.py    # HITL 审批端到端测试
│   ├── test_task_control.py # 任务生命周期控制测试
│   ├── test_templates.py   # 任务模板测试
│   ├── test_checkpoint.py  # LangGraph 检查点断点续跑测试
│   ├── test_agent_capabilities.py # Agent 能力回归测试（Multi-Agent Context / 参数检查 / 风险分级 / 失败回退 / Reviewer 上下文）
│   ├── test_budget.py      # LLM 预算测试
│   ├── test_search_timeliness.py # 搜索时效性测试
│   ├── test_rag_hybrid.py  # 混合检索测试
│   ├── test_tool_runtime.py # 统一 Tool Runtime 测试
│   ├── test_trace.py       # 执行追踪测试
│   ├── test_queue.py       # 任务队列测试
│   ├── test_task_storage.py # 任务存储后端测试
│   └── test_extract_text.py # LLM 响应归一化测试
├── scripts/                # 辅助脚本
│   ├── check.py            # 一键质量门禁（ruff + pytest，CI 同款）
│   └── zhipu_selftest.py   # 智谱 API 联调自测（Chat/Embedding/Rerank/工具）
├── .github/workflows/
│   └── ci.yml              # CI：quality-gate（ruff+pytest）+ security（pip-audit）
├── data/                   # 运行期产物（Chroma 向量库 / SQL 沙箱，不提交）
├── docs/                   # 技术文档（docx）
├── main.py                 # 薄入口 shim（透传 app.main.app，兼容 uvicorn main:app）
├── pyproject.toml          # 项目配置 + ruff/pytest 配置（exclude frontend）
├── requirements.txt        # Python 依赖
├── test.http               # 接口调试请求集（IDE HTTP Client）
├── AGENTS.md               # Coding Agent 任务入口说明
├── .env.example            # 环境变量模板（.env 不入库）
└── README.md               # 项目文档
```

---

## 配置项

### 应用与安全

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `APP_NAME` | `AI Agent Task Assistant` | 应用名称 |
| `DEBUG` | `false` | 调试模式 |
| `AUTH_ENABLED` | `false` | 是否启用 API 认证（生产建议开启，开启后 /api/v1 需携带 API Key） |
| `API_KEYS` | `""` | 逗号分隔的合法 API Key 列表（`Authorization: Bearer` / `X-API-Key`） |
| `CORS_ORIGINS` | `["http://localhost:5173", "http://127.0.0.1:5173"]` | 允许跨域的前端来源白名单（配合 allow_credentials） |

### LLM 与 Embedding

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `ANTHROPIC_AUTH_TOKEN` | (必填) | 智谱 GLM Anthropic 兼容端点 API Key（https://open.bigmodel.cn 申请） |
| `ANTHROPIC_BASE_URL` | `https://open.bigmodel.cn/api/anthropic` | 智谱 Anthropic 兼容端点地址 |
| `ZHIPU_MODEL` | `glm-4.5-air` | 默认模型 |
| `ZHIPU_TEMPERATURE` | `0.7` | 采样温度（0–2） |
| `ZHIPU_MAX_TOKENS` | `4096` | 单次最大输出 token 数 |
| `ZHIPU_OPENAI_BASE_URL` | `https://open.bigmodel.cn/api/paas/v4/` | 智谱 OpenAI 兼容端点（仅用于 Embedding） |
| `ZHIPU_EMBEDDING_MODEL` | `embedding-3` | 智谱 Embedding 模型 |

### Web 搜索（Tavily）与时效性

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `TAVILY_API_KEY` | (可选) | Tavily 搜索 API Key，未填则不注册 Web 搜索工具 |
| `WEB_SEARCH_MAX_RESULTS` | `5` | Web 搜索返回结果数 |
| `WEB_SEARCH_CACHE_TTL` | `21600` | 普通知识搜索结果的进程内缓存 TTL（秒，0=不缓存） |
| `WEB_SEARCH_TIME_SENSITIVE_CACHE_TTL` | `300` | 时效性搜索缓存 TTL（秒）；「今天/刚刚/当前」等强时效问题绕过缓存 |
| `WEB_SEARCH_ENABLE_LLM_INTENT` | `false` | 是否叠加 LLM 做搜索意图分类（默认用确定性规则） |
| `WEB_SEARCH_OFFICIAL_DOMAINS` / `WEB_SEARCH_TRUSTED_DOMAINS` / `WEB_SEARCH_LOW_QUALITY_DOMAINS` | `[]` | 来源质量分域：官方一手 / 可信 / 低质域名（支持子域匹配） |

### 向量库与 RAG

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `CHROMA_PERSIST_DIR` | `""` | Chroma 持久化目录（留空则用 data/chroma） |
| `VECTOR_STORE_BACKEND` | `chroma` | 向量库后端：chroma / pgvector |
| `EMBEDDING_DIM` | `2048` | pgvector 建表向量维度（须与 embedding 模型输出一致） |
| `RAG_CHUNK_SIZE` | `800` | RAG 分块大小 |
| `RAG_CHUNK_OVERLAP` | `100` | RAG 分块重叠 |
| `RAG_TOP_K` | `5` | RAG 检索默认返回数 |
| `RAG_DYNAMIC_CHUNKING` | `true` | 是否按文档类型动态选择分块参数（代码类大块/法律类小块） |
| `ENABLE_HYBRID_SEARCH` | `false` | 是否启用混合检索（BM25 关键词 + 向量语义，RRF 融合） |
| `HYBRID_VECTOR_RECALL_K` | `20` | 混合检索向量召回候选数 |
| `HYBRID_BM25_RECALL_K` | `20` | 混合检索 BM25 关键词召回候选数 |
| `HYBRID_RRF_K` | `60` | RRF 融合常数（越大越平滑） |
| `ENABLE_RERANK` | `false` | 是否启用召回后 Rerank 精排（.env 示例默认开启） |
| `ZHIPU_RERANK_MODEL` | `rerank` | 智谱 Rerank 模型编码 |
| `RETRIEVAL_TOP_K` | `20` | 向量召回候选数（rerank 前，上限 128） |
| `RERANK_TOP_K` | `5` | Rerank 精排后保留的结果数 |
| `RERANK_SCORE_THRESHOLD` | `0.0` | Rerank 相关性分数阈值，低于阈值的片段被过滤 |
| `SQLITE_SANDBOX_PATH` | `""` | SQL 工具沙箱库路径（留空则用 data/sandbox.db） |
| `ENABLE_LONG_TERM_MEMORY` | `false` | 是否在任务中启用长期记忆召回/写入 |

### 存储 / Checkpoint / 队列

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `POSTGRES_HOST` / `POSTGRES_PORT` / `POSTGRES_DB` / `POSTGRES_USER` / `POSTGRES_PASSWORD` | `localhost` / `5432` / `agent_db` / `postgres` / (空) | PostgreSQL 连接（任务持久化 / Checkpoint / pgvector 共用底层连接；提供 `postgres_dsn` / `postgres_async_dsn`） |
| `DB_CONNECT_TIMEOUT` | `3.0` | 数据库连接超时（秒） |
| `TASK_STORAGE_BACKEND` | `auto` | 任务存储后端：auto（PostgreSQL 优先，失败降级内存）/ postgres / sqlite / memory |
| `TASK_DB_PATH` | `""` | sqlite 任务库路径（留空则用 data/tasks.db） |
| `ENABLE_CHECKPOINTING` | `true` | 是否启用 LangGraph Checkpoint 持久化（崩溃恢复 / 断点续跑） |
| `CHECKPOINT_BACKEND` | `auto` | Checkpoint 后端：auto（PostgreSQL 优先）/ postgres / memory |
| `TASK_QUEUE_BACKEND` | `auto` | 任务队列后端：auto（Redis 优先，失败降级内存）/ redis / memory |
| `TASK_QUEUE_EMBEDDED_WORKER` | `true` | 是否在应用进程内启动内置 Worker（独立 `python -m app.worker` 部署时设 false） |
| `REDIS_QUEUE_KEY` | `agent:tasks:queue` | Redis 任务队列键名 |
| `QUEUE_DEQUEUE_TIMEOUT` | `1.0` | Worker 拉取任务超时（秒） |
| `REDIS_HOST` / `REDIS_PORT` / `REDIS_DB` | `localhost` / `6379` / `0` | Redis 连接（失败自动降级内存） |

### Agent 执行与工具审批

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `MAX_REPLAN_ITERATIONS` | `3` | 最大重新规划次数 |
| `MAX_EXECUTION_STEPS` | `10` | 单任务最大执行步骤 |
| `MAX_AGENT_STEPS` | `10` | 单任务最大 Agent 执行轮数（多 Agent 协作上限） |
| `SUB_AGENT_TIMEOUT_SECONDS` | `60.0` | 单个子 Agent / Reviewer 的 LLM 调用超时（秒），防止外部模型挂起导致无限等待 |
| `TOOL_APPROVAL_LEVEL` | `L2` | 触发 HITL 的最低工具风险等级：L2（仅高风险）/ L1（含业务影响）/ L0（全部工具） |
| `TOOL_APPROVAL_OVERRIDE_TOOLS` | `[]` | 强制要求人工审批的工具名列表（覆盖风险分级，优先级最高） |

### LLM 成本控制（单任务预算）

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `MAX_LLM_CALLS_PER_TASK` | `20` | 单任务最大 LLM 调用次数（0=不限） |
| `MAX_TOTAL_TOKENS_PER_TASK` | `50000` | 单任务最大 token 消耗（prompt+completion，0=不限） |
| `BUDGET_LIMIT_USD` | `0.0` | 单任务成本上限（美元，0=不限） |
| `LLM_INPUT_COST_PER_1M` | `0.0` | 每百万输入 token 价格（美元，用于成本估算） |
| `LLM_OUTPUT_COST_PER_1M` | `0.0` | 每百万输出 token 价格（美元，用于成本估算） |

### 后续规划（尚未定义配置项）

- RabbitMQ（分布式任务调度）与 Milvus（备选向量存储）为后续规划项，尚未在 `app/config/settings.py` 中定义，无对应配置项。
