# AGENTS.md

面向 coding agent 的任务入口。内容以仓库现状为准，与 README.md、pyproject.toml、
.github/workflows/ci.yml 保持一致。

## 项目定位

- 后端：Python 3.11+，**FastAPI** Web 框架 + **LangGraph** Agent 状态机（不是 django/rails；
  `app/config/settings.py` 是 Pydantic Settings，与 Django 无关）。入口为仓库根 `main.py`。
- 前端：React 18 + Vite + TypeScript SPA，位于 `frontend/`，由 npm 独立管理。
- 用途：企业级 AI Agent 任务执行助手（任务规划、工具调用、RAG 知识增强、长期记忆、自我反思）。

## 模块边界

| 模块 | 路径 | 职责 |
|------|------|------|
| Agent | `app/agent/` | LangGraph 状态机、Planner/Executor/Reflection 节点与 workflow 构建 |
| LLM | `app/llm/` | LLM Provider 抽象层、智谱 GLM 接入、Embedding 工厂 |
| Tools | `app/tools/` | 工具框架（BaseTool + ToolRegistry + 内置工具） |
| Memory | `app/memory/` | Redis 短期记忆（内存降级）+ Chroma 长期记忆 |
| RAG | `app/rag/` | 文档加载/分块/向量化/索引/检索/重排 + 服务门面 |
| Models | `app/models/` | Pydantic 数据模型与 API Schema |
| Services | `app/services/` | 业务逻辑层（task_service 任务管理、agent_service Agent 执行） |
| API | `app/api/` | FastAPI 路由（tasks/agent/knowledge/stats/tools）+ 全局异常处理 |
| Prompts | `app/prompts/` | Prompt 模板集中管理 |
| Config | `app/config/` | Settings、数据库连接、structlog 日志配置 |
| Frontend | `frontend/` | React SPA（四大界面），经 Vite 代理 `/api` 调后端 |

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
