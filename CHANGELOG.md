# CHANGELOG

本文件记录「企业级 Agent Operating Platform」升级改造各阶段的变更。
格式：修改内容 / 修改文件 / 数据库变化 / API 变化 / 测试结果 / 风险说明。

---

## [文档同步] README/AGENTS 对齐去 RAG 化与生产安全模式（2026-08-20）

### 修改内容

README.md 在「去 RAG 化」与「P0-1 生产安全模式」两轮改造后未同步，本次全面对齐仓库现状：

- **移除全部 RAG/知识库残留文档**：核心能力的 RAG 条目、技术栈 RAG 用途、RAG 模块行、
  Memory/RAG 数据流中的 RAG 段、知识库 API 5 个示例请求、检索增强（Hybrid+动态分块）章节、
  13 项 RAG 配置项（RAG_CHUNK_* / HYBRID_* / RERANK_* 等）、test_rag / test_rag_hybrid
  测试条目、项目结构中的 app/rag/ 与 knowledge.py / rag_tool.py / test.http；
- **补充生产安全模式文档**：核心能力新增「生产安全模式」条目；架构改进新增
  「生产安全模式（P0-1）」小节（环境分区/五处禁降级守卫/健康探针/任务门禁）；
  健康检查示例改为 /health + /health/live + /health/ready 三端点并附响应示例；
  生产部署建议表新增运行环境与表结构（Alembic）两行及探针接入说明；
  安装与启动新增生产部署命令（alembic upgrade head + ENVIRONMENT=production）；
- **补充 Alembic 迁移文档**：架构改进新增「数据库迁移（Alembic）」小节
  （基线迁移/双路径一致性/stamp 存量库/ASCII 约束）；
- **补充新能力文档**：执行追踪查询 API（GET /api/v1/traces）示例；配置项新增
  ENVIRONMENT / HEALTH_CHECK_TIMEOUT / HEALTH_CACHE_TTL；存储表注明 auto 降级仅开发模式生效；
- **修正过期描述**：PostgreSQL 从「预留」改为已实现（任务持久化/Checkpoint/pgvector）；
  Chroma 用途改为长期记忆；前端界面从「五大（含知识库）」改为四大；stats 响应去掉知识库计数字段；
  sqlparse 从「可选依赖」改为已入 requirements（配合 sqlglot）；检查点示例 SqliteSaver → PostgresSaver；
- **项目结构树重写**：对齐当前目录（新增 api/auth.py、v1/traces.py、config/eventloop.py、
  llm/budget.py、memory/vector_store*.py、tools/file_loader.py、services/health.py、
  migrations/、alembic.ini、CHANGELOG.md、docs/architecture_review.md、4 个运维脚本）。

AGENTS.md 小幅同步：API 模块行补 traces 路由与认证；Services 行补 task_repository；
启动与验证命令补充 4 个运维脚本（list_tasks / clear_tasks / run_cases / run_light_cases）。

### 修改文件

| 类别 | 文件 | 变更 |
|------|------|------|
| 文档 | `README.md` | 全文对齐去 RAG 化 + 生产安全模式 + Alembic 现状 |
| 文档 | `AGENTS.md` | 补 traces 路由 / task_repository / 运维脚本命令 |

### 数据库变化

- 无（纯文档变更）。

### API 变化

- 无（纯文档变更；文档内容对齐既有 API 现状）。

### 测试结果

- 纯文档变更，不涉及代码路径；`git diff` 复核无 RAG/知识库残留引用
  （grep 验证 RAG|knowledge|知识库|rerank|hybrid|BM25 均无命中）。

### 风险说明

- 无。本次仅修改 README.md / AGENTS.md 两个文档文件，不触碰任何代码与配置。

---

## [去 RAG 化] 移除 RAG / 知识库能力（2026-08-20）

### 修改内容

按产品决策移除 RAG（检索增强生成）相关全部功能；**保留并迁移**两处共享基础设施，
确保其余功能（长期记忆、文件解析、Agent 全链路）不受影响：

**整体删除（纯 RAG）：**

- `app/rag/` 检索管线整模块（base/indexer/retriever/hybrid_retriever/reranker/splitter/service）；
- 知识库 API 全部 5 个端点（`POST /knowledge/documents`、`POST /knowledge/upload`、
  `GET /knowledge/documents`、`DELETE /knowledge/documents`、`POST /knowledge/search`）；
- `rag_retrieval` 工具（注册、风险分级 L0 条目、权限矩阵 rag 类别）；
- 多 Agent 角色工具类别中的 rag（Research/Review/General 角色）；
- Executor Prompt 中的知识库检索表述（改为联网搜索/数据查询/文件读取）；
- Settings 中 RAG 专属配置 13 项（RAG_CHUNK_* / RAG_TOP_K / RAG_DYNAMIC_CHUNKING /
  ENABLE_HYBRID_SEARCH / HYBRID_* / ENABLE_RERANK / ZHIPU_RERANK_MODEL /
  RETRIEVAL_TOP_K / RERANK_TOP_K / RERANK_SCORE_THRESHOLD）；
- `/stats` 的知识库计数（knowledge_document_count / knowledge_chunk_count）；
- 前端知识库页面（路由 /knowledge、导航项「知识库」、apiClient.knowledge 命名空间、
  相关类型与测试；五大界面 → 四大界面）；
- 测试：test_rag.py / test_rag_hybrid.py 整文件，test_tools / test_new_endpoints /
  test_integration / conftest 中的 RAG 用例与 fixture（disable_rerank）。

**保留并迁移（共享基础设施）：**

- 向量存储 `app/rag/vector_store.py` + `vector_store_pg.py` → `app/memory/`
  （长期记忆 VectorLongTermMemory 的后端，能力不变：Chroma/pgvector 可插拔、
  租户/用户隔离过滤；`app/memory/__init__.py` 新增导出）；
- 文档加载器 `app/rag/loader.py` → `app/tools/file_loader.py`（file_processing
  工具的 PDF/DOCX/TXT/MD 解析能力不变；内部数据类改为自包含 ParsedDocument，
  不再依赖 rag.base）。

### 修改文件

| 类别 | 文件 | 变更 |
|------|------|------|
| 删除 | `app/rag/`（整目录） | RAG 检索管线 |
| 删除 | `app/api/v1/knowledge.py`、`app/tools/rag_tool.py` | 知识库路由 / RAG 工具 |
| 删除 | `tests/test_rag.py`、`tests/test_rag_hybrid.py` | RAG 测试 |
| 删除 | `frontend/src/features/knowledge/`（整目录） | 知识库页面 |
| 迁移 | `app/rag/vector_store*.py` → `app/memory/vector_store*.py` | 长期记忆向量库后端 |
| 迁移 | `app/rag/loader.py` → `app/tools/file_loader.py` | 文件解析加载器（自包含） |
| 修改 | `app/tools/builtins.py` / `security.py` / `risk.py` / `__init__.py` | 移除 rag_retrieval 与 rag 类别 |
| 修改 | `app/agent/multi_agent.py` | 角色工具类别去 rag |
| 修改 | `app/prompts/executor.py` | Prompt 去知识库表述 |
| 修改 | `app/api/router.py` / `deps.py` / `v1/stats.py` | 移除 knowledge 路由与 RAGService 注入、stats 去知识计数 |
| 修改 | `app/models/api_schemas.py` | 移除 knowledge 系列 Schema 与 Stats 字段 |
| 修改 | `app/config/settings.py` | 移除 13 项 RAG 配置；向量库配置注释改为「长期记忆使用」 |
| 修改 | `app/memory/long_term.py` / `__init__.py` | 向量库 import 路径与导出 |
| 修改 | `app/tools/file_processing.py` | 加载器 import 路径 |
| 修改 | `tests/conftest.py` / `test_tools.py` / `test_new_endpoints.py` / `test_integration.py` / `test_memory.py` | 去 RAG 用例；集成测试改 3 工具链路 |
| 修改 | `frontend/src/App.tsx` / `NavShell.tsx` / `lib/{apiClient,types,queryClient}.ts` / `apiClient.test.ts` / `DashboardPage.tsx` | 去知识库页面/导航/类型/统计 |
| 修改 | `requirements.txt` | 移除 rank-bm25 / jieba / langchain-text-splitters（纯 RAG 依赖） |
| 修改 | `.env.example` / `AGENTS.md` | 配置模板与文档同步 |

### 数据库变化

- 无（tasks 表与 Alembic 基线不受影响；Chroma/pgvector 中的
  `rag_documents` collection 数据成为孤岛，可按需手动清理，
  `long_term_memory` collection 继续服务长期记忆）。

### API 变化

- **移除**：`/api/v1/knowledge/*` 全部 5 个端点（破坏性，前端已同步）；
- **字段移除**：`GET /api/v1/stats` 响应不再含 `knowledge_document_count` /
  `knowledge_chunk_count`（前端已同步）；
- 其余端点契约不变；`/health/ready` 的 `vector_db` 组件保留
  （服务长期记忆向量库）。

### 测试结果

- 后端全量质量门禁（`python scripts/check.py` = ruff + pytest）：
  **ALL PASS，363 passed**（原 394 项，移除 test_rag / test_rag_hybrid 两个文件
  与各文件内 RAG 用例后，test_memory / test_integration / test_new_endpoints /
  test_tools / test_multi_agent / conftest 修复调整，全部通过）；
- 前端：`npm run lint`（tsc）通过，`npm run test`（vitest）21 passed。

### 风险说明

1. **破坏性 API 变更**：knowledge 端点与 stats 字段移除，依赖方（仅本前端）已同步；
   外部若有脚本调用这些端点需自行调整；
2. **依赖清理**：rank-bm25 / jieba / langchain-text-splitters 从 requirements 移除；
   本地 venv 残留不影响运行；chromadb / pgvector / pypdf / python-docx 保留
   （长期记忆与文件解析仍需要）；
3. **存量数据**：Chroma/pgvector 中原 RAG collection（rag_documents）不再被读写，
   不自动删除（避免误删长期记忆 collection），可运维手动清理；
4. **长期记忆行为不变**：向量库仅换了模块路径（app/rag → app/memory），
   数据目录、collection 名、隔离语义完全一致，存量记忆可直接延续使用。

---

## [P0-1 + Step 0] 生产环境安全模式 & Alembic 基线迁移（2026-08-19）

### 修改内容

**Step 0：Alembic 数据库迁移基线**

- 引入 Alembic 迁移机制，结束仅有 `create_all`、无法演进表结构的历史；
- 生成 `tasks` 表基线迁移（`0001_baseline`），与 ORM 定义逐列对齐；
- 约定双路径一致性：开发环境 `create_all` 引导、生产环境 `alembic upgrade head`
  演进，二者结构由测试保证不漂移（见 `tests/test_migrations.py`）；
- 存量库（已被 create_all 建表）首次接入时执行 `alembic stamp head` 标记基线。

**P0-1：生产环境安全模式**

1. **ENVIRONMENT 运行环境分区**（`development` | `production`，默认 development）：
   - 生产模式禁止一切静默降级，配置校验失败 / 依赖不可用即抛错；
   - 开发模式行为完全不变（保留降级开发体验与全部既有测试语义）。
2. **五处禁降级守卫**（仅生产模式生效）：
   - 任务存储：`TASK_STORAGE_BACKEND=memory` 拒绝启动；`auto` 模式 PostgreSQL
     不可用直接抛错（不再降级内存存储）；
   - Checkpoint：`CHECKPOINT_BACKEND=memory`（MemorySaver）拒绝；`auto` 等价
     `postgres`，失败抛错（不再降级 MemorySaver）；
   - 任务队列：`TASK_QUEUE_BACKEND=memory` 拒绝；`auto` 模式 Redis 不可达抛错
     （不再降级内存队列）；
   - 短期记忆：RedisShortTermMemory 新增 strict 模式（生产自动启用），Redis
     初始化/连接失败抛错（不再降级内存实现）；`create_short_term(use_redis=False)`
     生产拒绝；
   - Mock 工具：生产模式跳过 `email.send` 注册（当前实现为内存 Mock 通道，
   注册会静默吞掉外发请求）。
3. **基础设施健康检查**（`app/services/health.py`）：
   - `InfrastructureHealthChecker` 并行探测 7 类组件：database / queue /
     vector_db / llm / storage / checkpoint / redis，单组件超时（`HEALTH_CHECK_TIMEOUT`，
     默认 2s）不拖累整体；
   - 组件区分 core（down 阻断就绪）与非 core（信息性 / 开发模式设计内降级）；
   - `ReadinessGate` 带 TTL 缓存（`HEALTH_CACHE_TTL`，默认 5s）的就绪门禁。
4. **健康端点**：
   - `GET /health/live`：存活探针，进程存活即 200，不依赖外部基础设施；
   - `GET /health/ready`：就绪探针，核心组件不可用返回 503 + `ready=false` +
     组件明细（K8s 兼容语义）；
   - `GET /health`：保持原有行为不变（向后兼容）。
5. **启动 fail-fast**：生产模式 lifespan 启动即执行
   `verify_production_readiness`，核心依赖不可用直接终止启动。
6. **任务接收门禁**（`require_ready`）：生产模式下任务接收端点（创建 / 执行 /
   恢复 / 重试 / 模板运行 / 审批决策）在未就绪时返回 503 拒绝接收；
   开发模式直通（零开销）。
7. **缺陷修复（顺带）**：
   - 队列工厂 auto 探测在事件循环内（如 lifespan 初始化内嵌 Worker）时
     `asyncio.run` 嵌套报错被吞、必然静默降级内存队列——改为线程内探测，
     并为 Redis 客户端增加连接建立超时（2s）。

### 修改文件

| 类别 | 文件 | 变更 |
|------|------|------|
| 迁移 | `alembic.ini`（新增） | Alembic 配置（ASCII-only，规避 zh-CN Windows GBK 解码） |
| 迁移 | `migrations/env.py`（新增） | 迁移环境：URL 解析、metadata 绑定、compare_type |
| 迁移 | `migrations/script.py.mako`（新增） | 迁移脚本模板 |
| 迁移 | `migrations/versions/20260819_0001_baseline_tasks.py`（新增） | tasks 表基线迁移 |
| 配置 | `app/config/settings.py` | 新增 ENVIRONMENT / is_production / HEALTH_CHECK_TIMEOUT / HEALTH_CACHE_TTL |
| 守卫 | `app/services/task_service.py` | 生产禁内存存储（显式与 auto 降级路径） |
| 守卫 | `app/agent/checkpoint.py` | 生产禁 MemorySaver（显式与 auto 降级路径） |
| 守卫 | `app/queue/factory.py` | 生产禁内存队列；事件循环内探测修复 |
| 守卫 | `app/queue/redis_queue.py` | Redis 客户端增加 socket_connect_timeout |
| 守卫 | `app/memory/factory.py` | 生产禁内存短期记忆 |
| 守卫 | `app/memory/short_term.py` | RedisShortTermMemory strict 模式 |
| 守卫 | `app/tools/builtins.py` | 生产跳过 email.send（Mock 通道）注册 |
| 健康检查 | `app/services/health.py`（新增） | 检查器 / 就绪门禁 / 生产启动校验 |
| API | `app/api/health.py` | 新增 /health/live、/health/ready |
| API | `app/models/api_schemas.py` | 新增 LivenessResponse / ReadinessResponse / ComponentStatusSchema |
| API | `app/api/errors.py` | 新增 ServiceUnavailableException（503） |
| API | `app/api/deps.py` | 新增 require_ready 依赖 |
| API | `app/api/v1/tasks.py` | create / resume / retry / approve / reject 接入门禁 |
| API | `app/api/v1/agent.py` | execute 接入门禁 |
| API | `app/api/v1/templates.py` | run 接入门禁 |
| 启动 | `app/main.py` | 生产模式 lifespan 启动健康检查（fail-fast） |
| 依赖 | `requirements.txt` | 新增 alembic>=1.13.0 |
| 模板 | `.env.example` | 新增 ENVIRONMENT / HEALTH_* 配置说明 |
| 测试 | `tests/test_migrations.py`（新增） | 4 个迁移测试 |
| 测试 | `tests/test_production_mode.py`（新增） | 24 个生产模式测试 |

### 数据库变化

- 新增 `alembic_version` 表（Alembic 版本管理，执行迁移后自动创建）；
- `tasks` 表结构不变（基线迁移与现有 ORM/create_all 产物逐列一致，
  由 `test_migration_matches_create_all_schema` 保证）；
- **生产部署新增步骤**：`alembic upgrade head`（或存量库 `alembic stamp head`）。

### API 变化

- 新增 `GET /health/live`（200，存活探针）；
- 新增 `GET /health/ready`（就绪 200 / 未就绪 503，响应体含组件明细）；
- 既有 22 个端点契约不变；`GET /health` 行为不变；
- 行为收紧（仅生产模式）：任务接收端点在基础设施未就绪时返回 503
  （开发模式行为不变）。

### 测试结果

- 新增测试 28 个（迁移 4 + 生产模式 24），全部通过；
- 全量质量门禁（`python scripts/check.py` = ruff + pytest）：
  **394 passed**（366 既有 + 28 新增），ruff 无告警；
- 顺带修复 `scripts/` 遗留运维脚本的 19 处 lint 问题
  （import 排序 / 长行 / 死代码，纯格式修复不改逻辑），使门禁恢复全绿。

### 风险说明

1. **生产模式为收紧型变更**：`ENVIRONMENT=production` 下原先「静默降级可运行」
   的部署会启动失败或拒绝任务——这是设计意图（fail-fast），上线前需完成
   PostgreSQL / Redis / 向量库 / LLM 凭证检查；
2. **email.send 在生产模式不再注册**：接入真实 SMTP Provider 前生产环境无
   邮件能力（避免 Mock 通道静默吞件）；
3. **队列探测行为修复**：事件循环内 auto 探测原先必然降级内存队列（缺陷），
   现在会真实探测 Redis——开发机若启动了 Redis，内嵌 Worker 将改用 Redis
   队列（行为更正确，但与旧版表现不同）；
4. **alembic.ini 必须保持 ASCII**：zh-CN Windows 上 alembic CLI 以 GBK 读取
   ini，非 ASCII 注释会导致解析失败（文件内已有注释提醒）；
5. `HEALTH_CACHE_TTL`（默认 5s）内的就绪状态为缓存值：基础设施故障最长
   5s 后才反映到 /health/ready 与任务门禁（性能与实时性的折中）。

---
