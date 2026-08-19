# AGENTS.md

面向 coding agent 的任务入口。内容以仓库现状为准，与 README.md、pyproject.toml、
.github/workflows/ci.yml 保持一致。

## 项目定位

- 后端：Python 3.11+，**FastAPI** Web 框架 + **LangGraph** Agent 状态机（不是 django/rails；
  `app/config/settings.py` 是 Pydantic Settings，与 Django 无关）。应用工厂位于 `app/main.py`，
  仓库根 `main.py` 为薄入口（透传 `app`，兼容 `uvicorn main:app`）。
- 前端：React 18 + Vite + TypeScript SPA，位于 `frontend/`，由 npm 独立管理。
- 用途：企业级 AI Agent 任务执行助手（任务规划、多 Agent 协作、工具调用与高风险动作审批、
  任务暂停/恢复/取消/重试、任务模板、RAG 知识增强、长期记忆、自我反思）。

## 模块边界

| 模块 | 路径 | 职责 |
|------|------|------|
| Agent | `app/agent/` | LangGraph 状态机、Planner/Executor/Reflection 节点、Supervisor/SubAgents/Reviewer 多 Agent 协作、统一上下文（context.py/AgentContext）、需求提取与参数完整性检查（requirements.py）、interrupt 审批透传、workflow 构建 |
| Queue | `app/queue/` | 任务队列抽象（Redis 可靠队列 / 内存队列，auto 自动降级）+ 内置 Worker（`app/worker.py`） |
| LLM | `app/llm/` | LLM Provider 抽象层、智谱 GLM 接入、Embedding 工厂、单任务预算 |
| Tools | `app/tools/` | 工具框架（BaseTool + ToolRegistry + 内置工具）、统一执行管线（executor.py）、工具风险分级（risk.py）、搜索时效性（意图/新鲜度/缓存：search_intent.py / search_freshness.py / search_cache.py）、执行边界/审批钩子 |
| Memory | `app/memory/` | Redis 短期记忆（内存降级）+ Chroma 长期记忆 |
| RAG | `app/rag/` | 文档加载/分块/向量化/索引/检索/重排 + 混合检索（hybrid_retriever.py，BM25 缺失降级）+ 服务门面 |
| Models | `app/models/` | Pydantic 数据模型（含任务模板）与 API Schema、SQLAlchemy ORM |
| Services | `app/services/` | 业务逻辑层（task_service 任务管理、task_control 暂停/取消、template_service 模板、agent_service Agent 执行） |
| API | `app/api/` | FastAPI 路由（tasks/agent/knowledge/stats/tools/templates）+ 全局异常处理 |
| Prompts | `app/prompts/` | Prompt 模板集中管理（含多 Agent Supervisor/SubAgent/Reviewer） |
| Tracing | `app/tracing/` | 执行追踪（recorder.py：任务/节点 span/工具调用/Agent 步骤事件 + 环形缓存） |
| Config | `app/config/` | Settings（含 HITL 分级、Agent 上限、工具审批策略）、数据库连接、structlog 日志配置 |
| Frontend | `frontend/` | React SPA（五大界面：概览/任务控制台/任务模板/知识库/执行监控），经 Vite 代理 `/api` 调后端 |

## 关键能力速览（实现现状）

- **多 Agent 协作**：Supervisor 决策 single/multi_agent，SubAgents 串行执行（前序结果传递），Reviewer 合成最终结果。
  统一上下文 `AgentContext`（`app/agent/context.py`）沿 Supervisor→SubAgents→Reviewer 全链路结构化透传
  `original_user_query / extracted_requirements / tool_results / subagent_results` 等字段，原始需求与已提取参数不丢失。
- **Required Parameter Check**：`app/agent/requirements.py` 提供通用参数完整性检查，工具可声明 `required_params`；
  调用前合并「已提取参数 + query + 对话历史」确定性校验，缺失时阻断工具调用并向用户询问，不依赖 Prompt 提示模型。
- **HITL 审批（风险分级）**：`app/tools/risk.py` 定义 L0（只读 AUTO）/L1（有业务影响）/L2（高风险不可逆默认 HITL）
  三级模型；`HumanApprovalGate`（`app/agent/approval.py`）按 `settings.TOOL_APPROVAL_LEVEL`（默认 L2）判定，
  `TOOL_APPROVAL_OVERRIDE_TOOLS` 可强制指定工具审批。高风险调用触发 `interrupt()` 暂停（`awaiting_approval`），
  `POST /approvals/{id}/approve|reject` 决策后入队恢复。
- **任务生命周期**：`pause`（节点边界）/`resume`（断点续跑）/`cancel`/`retry`（可 `from_index`）；控制通过 `app/services/task_control.py` 注册表，Worker 在节点边界消费。
- **任务模板**：`app/services/template_service.py` 内置 4 个模板（market_research/document_analysis/code_review/general），`{var}` 渲染建任务；`/api/v1/templates` 提供 CRUD + run。
- **执行追踪**：`app/tracing/recorder.py` 记录任务 Run / 节点 span / 工具调用事件 / Agent 步骤事件（输入、上下文快照、提取参数、审批结果、耗时、错误），进程内环形缓存 + structlog。
- **重试 / 超时 / 回退**：工具瞬时失败有限重试（指数退避，幂等工具）；子 Agent / Reviewer LLM 调用超时保护（`SUB_AGENT_TIMEOUT_SECONDS`）；`MAX_AGENT_STEPS` 限制多 Agent 轮数防循环；失败显式返回错误状态。
- **搜索时效性与检索增强**：`web_search` 集成意图分类（GENERAL_KNOWLEDGE / TIME_SENSITIVE / NEWS 等）、来源新鲜度评分与缓存绕过；RAG 可选混合检索（`ENABLE_HYBRID_SEARCH`，BM25+向量 RRF，`rank_bm25` 缺失自动降级）+ 动态分块（`RAG_DYNAMIC_CHUNKING`）。
- **异步化**：API 只入队，Worker 消费执行；`TASK_QUEUE_BACKEND=memory` 单进程开发，`TASK_QUEUE_EMBEDDED_WORKER=false` 关闭内嵌 Worker（测试用）。
- **测试注意**：`tests/conftest.py` 的 autouse fixture 会 `get_settings.cache_clear()` 后 setenv，确保 `TASK_QUEUE_BACKEND=memory` 生效——**勿删**，否则 API 测试会走 Redis 探测（每次 ~48s 超时）。

## 启动与验证命令

后端（仓库根执行）：

```bash
pip install -r requirements.txt
uvicorn main:app --reload --host 0.0.0.0 --port 8000   # 启动服务
python scripts/check.py                                 # 统一门禁：ruff + pytest（CI quality-gate 同款）
```

前端（`frontend/` 目录执行）：

```bash
npm install
npm run dev    # 开发服务器（默认 :5173，自动代理 /api → :8000）
npm run lint   # tsc --noEmit 类型检查
npm run test   # vitest run
```

测试全部离线可跑：智谱 embedding 用 mock provider，Chroma 用临时目录，Redis/Tavily 无需真实服务
（见 `tests/conftest.py`）。

## 禁改边界

- `.env`：包含 API Key 等密钥，不入库、不读取内容、不修改。模板见 `.env.example`。
- `data/`：Chroma 向量库与 SQL 沙箱数据目录，运行期产物，不修改、不提交。
- `venv/`：本地虚拟环境，不修改、不提交。
- `frontend/` 已被 pyproject.toml 中 ruff 的 `extend-exclude` 排除：Python 侧 lint/测试不扫描前端，
  前端检查仅通过 `npm run lint` / `npm run test` 进行。
