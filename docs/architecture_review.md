# 架构评估报告（Architecture Review）

> 评估基线：仓库当前 HEAD（2026-08-19）。本报告为「企业级 Agent Operating Platform」升级改造的第一阶段产出，仅做分析、不改代码。
> 所有结论均附代码位置（`文件:行号`）作为证据，可直接跳转核对。

---

## 目录

1. [评估范围与方法](#1-评估范围与方法)
2. [当前架构总览](#2-当前架构总览)
3. [当前问题深度分析](#3-当前问题深度分析)
4. [技术债清单](#4-技术债清单)
5. [与企业级目标的差距映射](#5-与企业级目标的差距映射)
6. [改造路线建议与兼容性风险](#6-改造路线建议与兼容性风险)
7. [结论](#7-结论)

---

## 1. 评估范围与方法

本次评估覆盖以下维度，方法为「结构扫描 → 关键文件精读 → 全局关键词验证 → 交叉确认」：

| 维度 | 主要证据来源 |
|------|--------------|
| Backend / Agent Runtime | `app/agent/`、`app/services/`、`app/worker.py` 精读 |
| Tool Runtime / Security | `app/tools/`、`app/api/auth.py`、`app/tools/security.py` 精读 |
| Memory / RAG | `app/memory/`、`app/rag/` 精读 + 全局关键词搜索 |
| API / 数据模型 | 路由装饰器全量扫描 + ORM 模型精读 |
| 可观测性 / 测试 / 部署 | `app/tracing/`、`tests/`、`.github/workflows/ci.yml` + 测试收集（366 tests） |

**测试基线**：`pytest --collect-only` 收集到 **366 个测试**，全部离线可跑。这是后续所有改造的安全网。

---

## 2. 当前架构总览

### 2.1 总体架构图（现状）

```
                    React SPA (frontend/, Vite + TS)
                    概览 / 任务控制台 / 模板 / 知识库 / 执行监控
                              │  /api（Vite 代理）
                              ▼
                  FastAPI 应用工厂 (app/main.py)
        CORS ─ ErrorHandlerMiddleware ─ lifespan（工具注册/内嵌Worker/Checkpoint池）
                              │
             ┌────────────────┼──────────────────┐
             ▼                ▼                  ▼
      API 路由层         Task Queue           Health
    (app/api/v1/*)   (Redis / 内存, auto降级)  (/health 静态)
             │                │
             │ 入队即返回      ▼
             │         Worker (app/worker.py, 内嵌或独立进程)
             │                │ run_task / approval_resume
             ▼                ▼
        TaskService ◄──── AgentService (app/services/agent_service.py)
     (PG/SQLite/内存)         │
                              ▼
              LangGraph 状态机 (app/agent/workflow.py)
   START → Supervisor ─multi→ SubAgents → Reviewer → END
                │single
                ▼
           Planner → Executor → Reflection ─replan→ Replanner
                        │            (L0/L1/L2 风险分级 + interrupt HITL)
                        ▼
                 Tool Runtime (app/tools/)
     BaseTool + Registry + Executor + 权限矩阵(角色×类别) + 风险分级
                        │
          ┌─────────────┼─────────────┬──────────────┐
          ▼             ▼             ▼              ▼
     LLM(智谱GLM)   Memory(STM/LTM)  RAG(Chroma/   外部服务
     单任务预算      (Redis/内存,     pgvector,     (Tavily/GitHub/
     (budget.py)    Chroma/pgvector) BM25+RRF混合)  HTTP/Email)
```

### 2.2 Backend 模块

- **技术栈**：Python 3.11+ / FastAPI / Pydantic Settings（`app/config/settings.py`，约 130 个配置项）/ structlog（`app/config/logging.py`，生产 JSON / 调试控制台）。
- **应用工厂**：[main.py](../app/main.py) 的 `create_app()` + lifespan：初始化 PromptManager、数据目录、内置工具注册、可选内嵌 Worker、关闭时释放 Checkpoint 连接池（[app/main.py:25-78](../app/main.py#L25-L78)）。
- **服务层**：`task_service`（任务管理与持久化）、`agent_service`（Workflow 执行编排）、`task_control`（暂停/取消注册表）、`template_service`（模板）。
- **分层清晰**：API → Service → Agent/Tools/Queue/Memory/RAG，职责边界与 AGENTS.md 描述一致，无越层调用。

### 2.3 Frontend 模块

- React 18 + Vite 5 + TypeScript，npm 独立管理（[frontend/package.json](../frontend/package.json)）。
- 五大界面：Dashboard / Tasks（含创建、详情）/ Templates / Knowledge / Monitoring，组件库自建（CSS Modules）。
- 数据层：`@tanstack/react-query` + 自建 `apiClient`（[frontend/src/lib/apiClient.ts](../frontend/src/lib/apiClient.ts)），Vite 代理 `/api` → `:8000`。
- **无登录/认证页面**，无用户概念，直接调用后端 API。
- 测试：vitest + Testing Library（组件级单测，如 `CreateTaskForm.test.tsx`、`StatusPill.test.tsx`）。

### 2.4 Agent Runtime 与 Workflow

LangGraph 状态机（[app/agent/workflow.py:63-127](../app/agent/workflow.py#L63-L127)）：

```
START → supervisor ──multi_agent──→ sub_agents → reviewer → END
            │ single
            ▼
         planner → executor → reflection ─┬─replan→ replanner → executor
                                          ├─continue→ executor
                                          └─complete→ END
```

- **Supervisor**：LLM 决策 single / multi_agent 模式，失败回退单 Agent（[app/agent/multi_agent.py:138-220](../app/agent/multi_agent.py#L138-L220)）；子 Agent 串行执行（前序结果传递），`MAX_AGENT_STEPS` 防循环。
- **统一上下文**：`AgentContext`（[app/agent/context.py:30-111](../app/agent/context.py#L30-L111)）沿 Supervisor → SubAgents → Reviewer 全链路透传 `original_user_query / extracted_requirements / tool_results / subagent_results`，用 additive reducer 防覆盖（[app/agent/state.py:28](../app/agent/state.py#L28)）。
- **参数完整性检查**：`app/agent/requirements.py` 确定性提取 + 工具 `required_params` 校验，缺失即阻断并询问用户（不依赖 Prompt）。
- **Replanner**：基于反思结果重新规划，`plan_version` 递增，`MAX_REPLAN_ITERATIONS` 上限（[app/agent/planner_node.py:122-178](../app/agent/planner_node.py#L122-L178)）。
- **HITL**：`HumanApprovalGate`（[app/agent/approval.py](../app/agent/approval.py)）按风险等级触发 `interrupt()`，任务转 `awaiting_approval`，审批后经队列恢复（`Command(resume=...)`，[app/services/agent_service.py:637-666](../app/services/agent_service.py#L637-L666)）。
- **执行计划结构**：`plan.subtasks` 为**线性列表**（顺序执行，`current_task_index` 推进）——无 DAG、无并行、无条件分支（详见 §3.2）。

### 2.5 Tool Runtime

近期已完成统一 Tool Runtime 改造（Observe / Reason / Act / Remember / Interact 五类）：

- **抽象层**：`schema.py`（参数声明 required/optional）、`errors.py`（统一错误分类）、`context.py`（ExecutionContext）、`permissions.py`、`executor.py`（统一执行管线）。
- **注册表**：`ToolRegistry` 全局单例，内置约 14 个工具（[app/tools/builtins.py](../app/tools/builtins.py)）：datetime / calculator / sql_query / file_processing / web_search（Tavily）/ http_read / code_execution / data_transform / http_action / email（开发模式）/ database_write / github_create_pr / memory.* 四件套。（注：rag_retrieval 已随去 RAG 化移除，2026-08-20。）
- **权限矩阵**：角色（guest/user/admin）× 工具类别（system/rag/sql/file/network），fail-closed（[app/tools/security.py:38-70](../app/tools/security.py#L38-L70)）。
- **风险分级**：L0 只读 / L1 业务影响 / L2 高风险默认 HITL（`app/tools/risk.py`），`TOOL_APPROVAL_LEVEL` + `TOOL_APPROVAL_OVERRIDE_TOOLS` 可调。
- **搜索时效性**：意图分类 + 新鲜度评分 + 缓存绕过（`search_intent.py` / `search_freshness.py` / `search_cache.py`）。
- **防御性依赖**：`rank_bm25` 缺失自动降级纯向量检索（[app/rag/hybrid_retriever.py](../app/rag/hybrid_retriever.py)）。

### 2.6 Memory

| 层 | 实现 | 接入 Agent 链路？ |
|----|------|------------------|
| Short-Term | `InMemoryShortTermMemory`（字典+TTL）/ `RedisShortTermMemory`（Redis 失败自动降级内存，[app/memory/short_term.py:93-110](../app/memory/short_term.py#L93-L110)） | **否**（仅作为 Agent 可主动调用的工具 `memory.get/set/search/delete`，见 [app/tools/memory_tools.py:64-204](../app/tools/memory_tools.py#L64-L204)） |
| Long-Term | `VectorLongTermMemory`（Chroma/pgvector），**默认关闭**（`ENABLE_LONG_TERM_MEMORY=False`，[app/config/settings.py:196-198](../app/config/settings.py#L196-L198)） | 是（`_recall_memory` 检索注入 context，[app/services/agent_service.py:91-113](../app/services/agent_service.py#L91-L113)；`_remember_result` 任务成功后写入，[agent_service.py:439-440](../app/services/agent_service.py#L439-L440)） |

**核心事实（P0-2 依据）**：`conversation_history` 初始化只含当次输入——
[app/services/agent_service.py:287](../app/services/agent_service.py#L287)：

```python
"conversation_history": [{"role": "user", "content": goal}],
```

无会话（Session）概念、无历史上下文读取、无自动摘要、无 Context Window 管理、无历史压缩。**STM 存在但未形成 Agent 上下文闭环**。数据库中也没有 `conversation_sessions` / `conversation_messages` 表。

### 2.7 RAG

- **管线**：loader（pdf/docx/md/txt）→ splitter（动态分块：代码 1500/150、法律 500/50）→ embedding（智谱 embedding-3，2048 维）→ indexer → retriever → 可选 rerank（智谱 rerank 模型）→ service 门面。
- **混合检索**：BM25（jieba 分词）+ 向量召回，RRF 融合，`rank_bm25` 缺失降级（[app/rag/hybrid_retriever.py](../app/rag/hybrid_retriever.py)）。
- **向量库可插拔**：`chroma`（开发）| `pgvector`（生产，HNSW + metadata JSONB 过滤，[app/rag/vector_store_pg.py:33-260](../app/rag/vector_store_pg.py#L33-L260)）。
- **检索结果含 metadata**（来源、分数），`pgvector` 后端支持按 metadata 过滤与 `delete_by_source`——**这是未来文档版本管理与 ACL 的现成基础**，但当前 knowledge API 未使用。

### 2.8 Queue / Worker

- **Redis 队列**：LPUSH/BRPOP 持久 FIFO，**无 ACK / 无重投**（[app/queue/redis_queue.py:1-13](../app/queue/redis_queue.py#L1-L13) 注释自认），崩溃恢复依赖 LangGraph Checkpoint（重提任务断点续跑）。
- **auto 降级**：Redis 探测失败 → 静默降级内存队列（[app/queue/factory.py:19-58](../app/queue/factory.py#L19-L58)）。
- **Worker**：内嵌（`TASK_QUEUE_EMBEDDED_WORKER=true`）或独立进程（`python -m app.worker`）；消费循环异常不中断（[app/worker.py:33-113](../app/worker.py#L33-L113)）；支持 `run_task` 与 `approval_resume` 两类消息。
- **控制**：暂停/取消经 `TaskControlService` 注册表，**仅在节点边界生效**。

### 2.9 Database 与数据模型

- **ORM**：SQLAlchemy 2.0 async（asyncpg / aiosqlite），引擎与会话工厂在 [app/config/database.py](../app/config/database.py)。
- **任务存储**：`TASK_STORAGE_BACKEND=auto|postgres|sqlite|memory`，auto = PG 优先失败降级内存。
- **建表方式**：`Base.metadata.create_all`（[app/services/task_repository.py:214-221](../app/services/task_repository.py#L214-L221)）——**无 Alembic 迁移机制**（全局搜索仅 checkpoint 内部使用 LangGraph 自带的 autocommit 迁移）。
- **现有表**（仅 1 张业务表）：

`tasks`（[app/models/task_record.py:19-48](../app/models/task_record.py#L19-L48)）：`id / goal / context / owner_id / tenant_id / status / plan(JSON) / subtasks(JSON) / reflection(JSON) / plan_version / iteration_count / execution_mode / agent_results(JSON) / pending_approval(JSON) / approval_history(JSON) / final_result / error / created_at / updated_at`（时间为 String(64) ISO 格式），索引 `ix_tasks_owner_tenant`、`ix_tasks_status`。

- **`owner_id` / `tenant_id` 为无外键约束的裸字符串**（默认 "anonymous"/"default"）——没有 users / tenants / organizations / roles / permissions 表，多租户只是「字段预留」而非「模型约束」。
- **模板**：`TemplateService` 纯进程内存 dict（[app/services/template_service.py:94-103](../app/services/template_service.py#L94-L103)），自定义模板**重启即失**。
- **Trace**：进程内环形缓存（默认 500 条，[app/tracing/recorder.py:30](../app/tracing/recorder.py#L30)），不持久化。

### 2.10 API 清单（22 个端点）

| 模块 | 端点 | 说明 |
|------|------|------|
| health | `GET /health` | 静态 ok + 版本（[app/api/health.py:14-18](../app/api/health.py#L14-L18)） |
| tasks | `POST /api/v1/tasks/` | 创建任务（入队异步执行） |
| | `GET /api/v1/tasks/` | 任务列表 |
| | `GET /api/v1/tasks/{id}` | 任务详情/状态 |
| | `POST /{id}/pause` `/{id}/resume` `/{id}/cancel` `/{id}/retry` | 生命周期控制（retry 支持 from_index） |
| | `GET /{id}/approvals` | 审批请求列表 |
| | `POST /{id}/approvals/{aid}/approve`、`/reject` | 审批决策（[app/api/v1/tasks.py:320-426](../app/api/v1/tasks.py#L320-L426)） |
| agent | `POST /api/v1/tasks/{id}/execute` | 重新执行/断点续跑（入队） |
| templates | `GET/POST /api/v1/templates/`、`GET/PUT/DELETE /{id}`、`POST /{id}/run` | 模板 CRUD + 渲染建任务 |
| knowledge | `POST /documents`、`POST /upload`、`GET /documents`、`DELETE /documents`、`POST /search` | 知识库摄取/列表/删除/检索 |
| tools | `GET /api/v1/tools` | 工具清单 |
| stats | `GET /api/v1/stats` | 任务/状态/工具/文档计数 |
| traces | `GET /api/v1/traces`、`GET /{task_id}` | 进程内 Trace 查询 |

**认证现状**：`AUTH_ENABLED=false` 时全放行且默认 admin；开启后校验静态 API Key（Bearer / X-API-Key），**身份（user_id / role / tenant_id）完全由请求头 `X-User-Id` / `X-User-Role` / `X-Tenant-Id` 自声明**（[app/api/auth.py:40-78](../app/api/auth.py#L40-L78)）——**可任意伪造，无用户库、无会话、无 JWT/OAuth2/OIDC/SAML**。

### 2.11 部署与 CI

- **CI**（[.github/workflows/ci.yml](../.github/workflows/ci.yml)）：quality-gate（`scripts/check.py` = ruff + pytest）+ security（pip-audit，忽略 1 个已评估漏洞）。
- **无 Dockerfile / docker-compose / K8s manifests / 迁移脚本 / 生产部署文档**。
- 运行产物（`data/`、日志、`*.out.txt` 报告文件）散落仓库根目录。

### 2.12 测试覆盖

- **后端**：366 个测试，24 个测试文件，覆盖：agent 流程、多 Agent、审批、预算、checkpoint、集成、LLM、memory、queue、RAG（含 hybrid 降级）、搜索时效、任务控制/存储、模板、工具运行时、trace、API。
- **conftest 关键设计**：autouse fixture 强制 `TASK_QUEUE_BACKEND=memory` + 关闭内嵌 Worker + 重置 trace recorder（[tests/conftest.py:28-49](../tests/conftest.py#L28-L49)）——**勿删**，否则 API 测试走 Redis 探测（约 48s 超时）。
- **缺口**：无 E2E（Playwright）、无负载测试、无 Agent 基准数据集、无评测框架。

---

## 3. 当前问题深度分析

### 3.1 Memory —— **未闭环（P0-2 核心依据）**

**结论：Short-Term Memory 没有真正进入 Agent Context。**

| 检查项 | 现状 | 证据 |
|--------|------|------|
| STM 注入 Planner/Supervisor prompt | ❌ 不存在 | `conversation_history` 仅含当次 goal（[agent_service.py:287](../app/services/agent_service.py#L287)）；全局搜索 `create_short_term` 仅 2 处调用：API deps（供 knowledge 路由）与 memory 工具 |
| 会话模型 | ❌ 无 `conversation_sessions` / `conversation_messages` 表 | ORM 仅 `tasks` 一张表 |
| 最近上下文读取 | ❌ 每个任务独立执行，任务间无对话延续 | 任务创建即新 state |
| 自动摘要 / 历史压缩 / Context Window 管理 | ❌ 均不存在 | 无相关代码 |
| Session 恢复 | ❌ Checkpoint 只恢复「单任务内」状态（thread_id=task_id），非会话级 | [agent_service.py:226-227](../app/services/agent_service.py#L226-L227) |
| LTM 接入 | ✅ 已接入但默认关闭 | `ENABLE_LONG_TERM_MEMORY=False`（[settings.py:196-198](../app/config/settings.py#L196-L198)） |
| STM 作为工具 | ✅ Agent 可主动 `memory.get/search` | [memory_tools.py:64-204](../app/tools/memory_tools.py#L64-L204) |

**影响**：多轮对话场景下，Agent 每轮都「失忆」，只靠 LTM（默认关）兜底；`AgentContext.conversation_history` 字段形同虚设。目标链路「User Message → Session → STM → Context Builder → Planner → … → LTM」中，**Session 与 Context Builder 两个环节整体缺失**。

### 3.2 Task System

**已具备**（质量较好）：10 态生命周期状态机（pending/planning/executing/reflecting/replanning/awaiting_approval/paused/cancelled/completed/failed，[app/models/task.py:14-26](../app/models/task.py#L14-L26)）、节点边界暂停/取消、断点续跑（Checkpoint thread_id=task_id）、`from_index` 重试、审批恢复。

**问题**：

1. **队列无 ACK/重投**：Worker 从 Redis BRPOP 取走任务后若进程崩溃，任务消息已丢失，**没有任何机制自动重投**——只能依赖用户手动重新 execute（依赖 checkpoint 不重跑已完成节点）。这是可靠性最大短板（[redis_queue.py:1-13](../app/queue/redis_queue.py#L1-L13)）。
2. **暂停粒度粗**：仅节点边界响应（[agent_service.py:543-548](../app/services/agent_service.py#L543-L548)），长节点（如 LLM 挂起）内无法暂停；有 `SUB_AGENT_TIMEOUT_SECONDS` 超时保护但主 Executor LLM 调用无统一超时。
3. **无 Scheduler**：无 cron / 延迟任务 / 周期任务，无 `scheduled_tasks` 概念。
4. **计划为线性列表**：`subtasks` 顺序执行（`current_task_index`），**无 DAG、无并行、无条件分支、无节点级 retry/timeout 策略**——P1-3 需要从执行模型层面扩展。
5. **模板不持久化**：进程内存 dict，重启丢失自定义模板。

### 3.3 Security

| 检查项 | 现状 |
|--------|------|
| Authentication | 静态 API Key 列表（逗号分隔字符串），无用户库、无密钥轮换、无撤销机制 |
| Authorization | 角色×工具类别 5×3 矩阵（工具层）；API 层仅 `require_non_guest`（知识库写操作）+ `can_access_task`（任务归属） |
| 身份可信性 | **`X-User-Role: admin` 由请求方自声明即生效**（[auth.py:72](../app/api/auth.py#L72)）——认证与授权完全脱钩，静态 Key 持有者可冒充任意用户/租户 |
| Multi-Tenant | `tenant_id` 为裸字符串字段，无 tenants 表、无租户隔离校验（任务查询有 owner+tenant 过滤，但知识库文档**全租户共享**） |
| Permission 粒度 | 无 `task:create / tool:execute / approval:approve` 等细粒度权限；审批决策端点**仅校验任务访问权，未校验审批者角色**（[tasks.py:320-426](../app/api/v1/tasks.py#L320-L426)） |
| OAuth2/OIDC/SAML | 无任何预留 |

**结论**：当前是「单租户演示级」安全模型。RBAC（P0-3）需要新建 users/tenants/organizations/roles/permissions/role_permissions/user_roles 全套模型，并将身份来源从「请求头自声明」迁移为「服务端签发/校验」（现有 `X-User-Id` 机制可作为内部服务间调用的过渡保留，但必须与新的身份体系打通）。

### 3.4 Observability

| 检查项 | 现状 |
|--------|------|
| Trace | 自研 `TraceRecorder`：OpenTelemetry **风格**（span 语义）但**未接入 OTel SDK**，无 OTLP 导出，无跨服务传播（trace_id 仅透传字符串） |
| Trace 持久化 | 进程内环形缓存 500 条（[recorder.py:30](../app/tracing/recorder.py#L30)），重启即失，多 Worker 进程各自独立 |
| Logging | structlog，生产 JSON 格式，task_id contextvars 绑定——质量良好 |
| Metrics | **无**。无 Prometheus、无 `/metrics` 端点、无 task_success_rate / latency / retry_rate / tool_failure_rate / rag_hit_rate / approval_wait_time |
| Cost Tracking | 仅单任务预算（`budget.py`：调用次数/token/USD 上限 + `UsageCallbackHandler` 自动采集）；**无租户/用户/Agent 级汇总**，stats 端点不暴露成本 |
| 健康检查 | 仅静态 `/health`；无 `/health/live`、`/health/ready`，无基础设施探测 |

**结论**：可观测性处于「开发调试」水平，与生产级（OTel + Prometheus + Grafana + Jaeger）差距是全量的，但 `TraceRecorder` 的事件模型（run/node span/tool call/usage）与目标高度同构，**适合作为 OTel instrumentation 的采集点改造基础，而非推倒重写**。

### 3.5 Production Readiness

1. **静默降级遍地（P0-1 核心依据）**——生产环境不可接受的降级路径全部静默发生，仅打日志：

   | 降级点 | 位置 | 后果 |
   |--------|------|------|
   | 任务存储 auto→内存 | `TASK_STORAGE_BACKEND=auto` | 重启丢全部任务记录 |
   | Checkpoint auto→MemorySaver | [app/agent/checkpoint.py:50-135](../app/agent/checkpoint.py#L50-L135) | 多 Worker 不共享、重启丢断点 |
   | 队列 auto→内存 | [app/queue/factory.py:19-58](../app/queue/factory.py#L19-L58) | 单进程限流、丢持久性 |
   | STM Redis→内存 | [short_term.py:98-110](../app/memory/short_term.py#L98-L110) | 多实例不共享 |
   | Embedding mock | 测试/无 Key 场景 | 生产误用产生无效向量 |

2. **无 `ENVIRONMENT` 配置**：settings 中只有 `DEBUG`，无 development/production 模式区分，无「生产模式禁止降级」的启动断言。
3. **无基础设施健康检查**：启动时不探测 PostgreSQL/Redis/向量库/LLM Provider，核心依赖挂了服务照常接收任务然后失败。
4. **无迁移机制**：`create_all` 只能建新表，无法演进表结构；新增 users/roles/conversations 等表**必须引入 Alembic**（这是 P0 各项的前置依赖）。
5. **无容器化/部署物**：无 Dockerfile/compose；`data/`、日志、报告文件混入仓库根目录。
6. **时间字段为 String ISO**（`task_record.py:41-42`）：无法用数据库时间函数聚合查询（成本/延迟统计将受限）。

---

## 4. 技术债清单

按严重度排序（S1 阻塞生产 / S2 功能缺口 / S3 工程质量）：

| # | 级别 | 技术债 | 影响 | 对应改造项 |
|---|------|--------|------|-----------|
| 1 | S1 | 静默降级链（存储/Checkpoint/队列/STM） | 生产数据丢失、假高可用 | P0-1 |
| 2 | S1 | 身份自声明（X-User-Role 可伪造），无用户体系 | 安全空心化 | P0-3 |
| 3 | S1 | 无 ENVIRONMENT 分区与健康就绪探针 | 故障任务照收 | P0-1 |
| 4 | S1 | STM 未接入 Agent 上下文闭环 | 多轮对话失忆 | P0-2 |
| 5 | S2 | 队列无 ACK/重投 | Worker 崩溃丢任务 | P0-1（顺带）/ P1-1 |
| 6 | S2 | 无 Metrics/OTel 导出，Trace 不持久化 | 生产不可运维 | P0-4 |
| 7 | S2 | 无评价体系（仅 Reflection 自检） | 质量不可度量 | P0-5 |
| 8 | S2 | 计划线性执行，无 DAG/并行/条件 | 复杂任务编排受限 | P1-3 |
| 9 | S2 | 无 Scheduler/Event Trigger | 自动化能力缺失 | P1-1/P1-2 |
| 10 | S2 | 知识库无版本/ACL/Citation | 企业知识治理缺失 | P1-4 |
| 11 | S2 | 成本仅单任务预算，无多级预算与看板 | 成本不可治理 | P2-2 |
| 12 | S3 | 无 Alembic，create_all 建表 | 表结构无法演进 | P0 前置 |
| 13 | S3 | 模板/Trace 进程内存态 | 重启丢失 | P0-1/P0-4 |
| 14 | S3 | 时间字段 String 存储 | 聚合查询受限 | 随迁移解决 |
| 15 | S3 | 无 Docker/部署物；仓库根散落运行产物 | 部署不可复制 | P2-3 前置 |
| 16 | S3 | 无 E2E/负载/基准测试 | 升级回归风险 | P2-3 |

---

## 5. 与企业级目标的差距映射

| 目标能力 | 现状 | 差距 | 复用基础 |
|----------|------|------|----------|
| **P0-1 生产安全模式** | 无 ENVIRONMENT；4 处静默降级；静态 /health | 全量新增（ENVIRONMENT + 启动检查 + live/ready） | settings.py 结构良好，易加配置；deps.py 已有单例注入点 |
| **P0-2 STM 闭环** | STM 实现 + 工具齐全，但 conversation_history 空转 | 新增 conversation_sessions/messages 表 + Context Builder（最近上下文/摘要/压缩） | `AgentContext.conversation_history` 字段与 `format_context` 已预留消费端（[context.py:76-92](../app/agent/context.py#L76-L92)） |
| **P0-3 RBAC** | 静态 Key + 头自声明角色 + 5×3 工具矩阵 | 全套 users/tenants/org/roles/permissions 模型 + 身份签发 | `ToolContext`（user_id/tenant_id/role）已是全链路载体，API/工具/记忆均按其隔离——**换成服务端身份后下游零改动** |
| **P0-4 OTel** | 自研 recorder（OTel 风格），structlog 良好 | 接 OTel SDK + OTLP 导出 + Prometheus /metrics + 成本聚合 | recorder 事件模型同构，budget.py 已自动采集 usage |
| **P0-5 Evaluation** | 仅 Reflection 自检 | 新增 evaluation_results 表 + Evaluator（6 指标） + Pass/Retry/Replan 决策钩子 | Reflection 节点已具备打分输出结构（should_replan 路由现成） |
| **P1-1 Scheduler** | 无 | scheduled_tasks 表 + cron/延迟/周期调度器 | 队列与 Worker 现成，调度器只需按时入队 |
| **P1-2 Event Trigger** | 无 | Webhook 端点 + Trigger Engine | TaskMessage 消息模型可扩展 event 来源 |
| **P1-3 Workflow DAG** | 线性 subtasks 顺序执行 | workflow/workflow_nodes/edges 表 + 并行/条件/审批/延时节点 | LangGraph 本身支持并行图；HITL interrupt 已验证 |
| **P1-4 知识库增强** | 检索含 metadata；pgvector 支持 metadata 过滤 | document_versions + ACL + Citation 输出约束 | vector_store_pg 的 JSONB 过滤与 delete_by_source 是版本/ACL 直接基础 |
| **P2-1 Connector** | GitHub/HTTP/Email 等点状工具 | Connector 抽象（OAuth/发现/限流） | Tool Runtime 的 Registry/权限/风险模型可承载 |
| **P2-2 Cost Mgmt** | 单任务预算 | 多级预算 + Cost Dashboard | budget.py 的核算逻辑 + recorder 的 cost_usd 字段 |
| **P2-3 测试增强** | 366 单测 + 组件测试 | E2E/负载/基准 | 测试基建（conftest 离线化）成熟 |

**总体判断**：该项目**架构分层清晰、抽象设计到位**（ToolContext 贯穿、AgentContext 透传、TraceRecorder 事件模型、可插拔后端），升级路径是「**在既有抽象上补企业能力**」而非重写。最大结构性缺口是 **身份体系（P0-3）与数据库迁移（Alembic）**——它们是几乎所有新表的前提。

---

## 6. 改造路线建议与兼容性风险

### 6.1 建议执行顺序（在既有 P0→P2 基础上的依赖调整）

```
0.  Alembic 引入 + 现有 tasks 表基线迁移          ← 所有用户新表的前置
1.  P0-1 ENVIRONMENT + 健康检查 + 生产禁降级       ← 不依赖其他项，最先落地
2.  P0-3 RBAC（users/tenants/roles/permissions）   ← P0-2 的 session 需要 user/tenant 外键
3.  P0-2 STM 闭环（conversation_sessions/messages + Context Builder）
4.  P0-4 OTel（recorder 改造为 OTel 采集点 + /metrics + 成本聚合）
5.  P0-5 Evaluation Engine
6.  P1-1 → P1-2 → P1-3 → P1-4（可并行度高）
7.  P2-1 → P2-2 → P2-3
```

### 6.2 兼容性红线（改造期间必须遵守）

1. **不破坏既有 API 行为**：22 个端点的请求/响应契约保持不变（新增字段允许，删除/改名不允许）——AGENTS.md 明文要求。
2. **366 个测试必须持续全绿**：每阶段提交前跑 `python scripts/check.py`；`tests/conftest.py` 的 autouse fixture **勿删**。
3. **`ToolContext` 是身份兼容的关键 seam**：RBAC 落地时保持其字段（user_id/tenant_id/role）不变，下游（工具权限矩阵、记忆隔离、任务归属校验）零改动；生产模式切换为服务端身份来源，开发模式保留 header 自声明以便本地调试与测试复用。
4. **降级开关语义**：`auto` 后端保留（开发体验），但生产模式（`ENVIRONMENT=production`）下 auto 视同强制真实后端，探测失败**启动即失败**——这是行为收紧而非破坏。
5. **前端**：ruff 排除 `frontend/`，前端检查独立走 `npm run lint` / `npm run test`；新增登录/RBAC 页面时保持既有五页结构不推翻。
6. **每阶段产出 CHANGELOG**（修改内容/文件/数据库变化/API 变化/测试结果/风险说明），新功能五件套齐全（Implementation/Migration/Unit Test/Integration Test/Documentation）。

### 6.3 主要技术风险

| 风险 | 缓解措施 |
|------|----------|
| Alembic 引入时与 create_all 冲突（双建表路径） | 基线迁移后 create_all 保留给全新空库初始化，两者表结构由单一 metadata 源保证一致；测试中验证两条路径 |
| RBAC 引入后存量 `tasks.owner_id/tenant_id` 无主 | 迁移脚本创建默认租户/系统用户，存量数据归入 default 租户 admin，不丢数据 |
| OTel 依赖增加拖慢启动/测试 | OTel SDK 按 `OTEL_ENABLED` 开关懒加载；测试默认关闭（保持 366 测试离线可跑） |
| conversation 表高频写入（每消息一行的 trace 级写入） | 消息写入与任务执行解耦（API 层写会话，Worker 层只读），并按 session 聚合 token_count |
| 评测引擎引入拖慢任务链路 | Evaluator 默认异步事后执行（evaluation_results 落库），不阻塞主 Workflow；Retry/Replan 钩子按配置启用 |
| Windows 本地开发（当前环境）与生产 Linux 差异 | Docker 化优先（P2），CI 已在 ubuntu 验证 |

---

## 7. 结论

当前项目是一个**工程质量良好、抽象到位的 Agent 任务执行框架**：统一上下文透传、风险分级 HITL、参数完整性检查、单任务预算、可插拔后端、366 个离线测试——这些是升级为企业级平台的坚实底座。

距离「企业级 Agent Operating Platform」的**四大结构性缺口**：

1. **身份与权限空心**（角色靠请求头自声明，无用户/租户模型）——P0-3，且是多数新表的前置；
2. **上下文闭环断裂**（STM 存在但未接入，无会话模型）——P0-2；
3. **生产安全缺位**（静默降级链 + 无环境分区 + 无就绪探针 + 无迁移机制）——P0-1；
4. **可观测性与质量度量停留在开发态**（Trace 进程内存态、无 Metrics、无客观评价）——P0-4/P0-5。

建议按 §6.1 顺序推进（Alembic → P0-1 → P0-3 → P0-2 → P0-4 → P0-5 → P1 → P2），全程守住 §6.2 的兼容性红线。

---

*报告完。下一步进入 P0-1（生产环境安全模式）实施前，需先完成 Alembic 基线迁移的前置决策确认。*
