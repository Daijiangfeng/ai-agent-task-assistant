"""
应用配置管理模块。
使用 pydantic-settings 实现分层配置，支持 .env 文件和环境变量覆盖。
"""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# 项目根目录（ai-agent-task-assistant/）
BASE_DIR = Path(__file__).resolve().parent.parent.parent


class Settings(BaseSettings):
    """应用全局配置，所有配置项均可通过环境变量或 .env 文件覆盖。"""

    # ---- 应用配置 ----
    APP_NAME: str = "AI Agent Task Assistant"
    APP_VERSION: str = "0.1.0"
    DEBUG: bool = False

    # ---- CORS ----
    CORS_ORIGINS: list[str] = Field(
        default=[
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        ],
        description="允许跨域访问的前端来源白名单（配合 allow_credentials 使用）",
    )

    # ---- API 认证 ----
    AUTH_ENABLED: bool = Field(
        default=False,
        description="是否启用 API 认证（生产环境必须开启）。"
        "开启后所有 /api/v1 接口必须携带有效 API Key。",
    )
    API_KEYS: str = Field(
        default="",
        description="逗号分隔的合法 API Key 列表（生产环境配置）。"
        "请求需通过 Authorization: Bearer <key> 或 X-API-Key: <key> 携带。",
    )

    # ---- 智谱 LLM 配置（Anthropic 兼容端点） ----
    ANTHROPIC_AUTH_TOKEN: str = Field(
        default="",
        description="智谱 GLM Anthropic 兼容端点 API Key（https://open.bigmodel.cn 申请）",
    )
    ANTHROPIC_BASE_URL: str = Field(
        default="https://open.bigmodel.cn/api/anthropic",
        description="智谱 Anthropic Compatible API 基础地址",
    )
    ZHIPU_MODEL: str = Field(default="glm-4.5-air", description="默认模型名称")
    ZHIPU_TEMPERATURE: float = Field(default=0.7, ge=0.0, le=2.0)
    ZHIPU_MAX_TOKENS: int = Field(default=4096, ge=1)

    # ---- 智谱 OpenAI 兼容端点（Embedding 专用） ----
    ZHIPU_OPENAI_BASE_URL: str = Field(
        default="https://open.bigmodel.cn/api/paas/v4/",
        description="智谱 OpenAI Compatible API 基础地址（用于 Embedding）",
    )
    ZHIPU_EMBEDDING_MODEL: str = Field(
        default="embedding-3", description="智谱 Embedding 模型名称"
    )

    # ---- PostgreSQL ----
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_DB: str = "agent_db"
    POSTGRES_USER: str = "postgres"
    POSTGRES_PASSWORD: str = ""
    DB_CONNECT_TIMEOUT: float = Field(
        default=3.0, ge=0.5, le=30, description="数据库连接超时（秒）"
    )

    # ---- 任务存储（TaskService 持久化后端） ----
    # auto: 优先 PostgreSQL，不可用则降级内存；postgres/sqlite/memory: 强制指定。
    TASK_STORAGE_BACKEND: str = Field(
        default="auto",
        description=(
            "任务存储后端：auto | postgres | sqlite | memory"
            "（auto=PostgreSQL 优先，失败降级内存）"
        ),
    )
    TASK_DB_PATH: str = Field(
        default="", description="sqlite 任务库路径，留空则用 data/tasks.db"
    )

    # ---- Redis ----
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0

    # ---- Web Search (Tavily) ----
    TAVILY_API_KEY: str = Field(default="", description="Tavily 搜索 API Key")
    WEB_SEARCH_MAX_RESULTS: int = Field(default=5, ge=1, le=20)
    WEB_SEARCH_CACHE_TTL: int = Field(
        default=21600,
        ge=0,
        description="普通知识搜索结果的进程内缓存 TTL（秒）；0 表示不缓存",
    )
    WEB_SEARCH_TIME_SENSITIVE_CACHE_TTL: int = Field(
        default=300,
        ge=0,
        description="时效性搜索结果的缓存 TTL（秒）；'今天/刚刚/当前'等强时效问题将绕过缓存",
    )
    WEB_SEARCH_ENABLE_LLM_INTENT: bool = Field(
        default=False,
        description="是否叠加 LLM 做意图分类（默认关闭，使用确定性规则）",
    )
    # 来源质量分域配置（通用机制，可按需补充候选域名；不做硬编码单点判断）。
    WEB_SEARCH_OFFICIAL_DOMAINS: list[str] = Field(
        default=[], description="官方一手来源域名（含子域匹配）"
    )
    WEB_SEARCH_TRUSTED_DOMAINS: list[str] = Field(
        default=[], description="可信来源域名"
    )
    WEB_SEARCH_LOW_QUALITY_DOMAINS: list[str] = Field(
        default=[], description="低质/内容农场域名"
    )

    # ---- Chroma 向量库 ----
    CHROMA_PERSIST_DIR: str = Field(
        default="", description="Chroma 持久化目录，留空则用 data/chroma"
    )

    # ---- 向量库后端（可插拔） ----
    # chroma: 进程内持久化，适合开发/单机 Demo；
    # pgvector: PostgreSQL 扩展，适合生产多实例部署（Milvus/Qdrant 可同理扩展）。
    VECTOR_STORE_BACKEND: str = Field(
        default="chroma", description="向量库后端：chroma | pgvector"
    )
    EMBEDDING_DIM: int = Field(
        default=2048,
        ge=1,
        description=(
            "pgvector 建表向量维度，必须与 embedding 模型输出一致"
            "（智谱 embedding-3 默认 2048）"
        ),
    )

    # ---- RAG 配置 ----
    RAG_CHUNK_SIZE: int = Field(default=800, ge=100)
    RAG_CHUNK_OVERLAP: int = Field(default=100, ge=0)
    RAG_TOP_K: int = Field(default=5, ge=1, le=50)

    # ---- RAG 动态分块（按文档类型选择 chunk 参数） ----
    # 开启后按文档 type/扩展名匹配分块画像：代码类大块（1500/150），
    # 法律类小块（500/50），其余用 RAG_CHUNK_SIZE/RAG_CHUNK_OVERLAP 默认值。
    RAG_DYNAMIC_CHUNKING: bool = Field(
        default=True, description="是否按文档类型动态选择分块参数"
    )

    # ---- RAG Hybrid Search（BM25 + 向量检索 RRF 融合） ----
    ENABLE_HYBRID_SEARCH: bool = Field(
        default=False,
        description=(
            "是否启用混合检索：BM25 关键词召回 + 向量语义召回经 "
            "Reciprocal Rank Fusion 融合（提升专有名词/编号/型号命中）"
        ),
    )
    HYBRID_VECTOR_RECALL_K: int = Field(
        default=20, ge=1, le=128, description="混合检索中向量召回候选数"
    )
    HYBRID_BM25_RECALL_K: int = Field(
        default=20, ge=1, le=128, description="混合检索中 BM25 关键词召回候选数"
    )
    HYBRID_RRF_K: int = Field(
        default=60, ge=1, description="RRF 融合常数（k，越大越平滑）"
    )

    # ---- RAG Rerank 精排配置（智谱 rerank 模型） ----
    ENABLE_RERANK: bool = Field(
        default=False, description="是否启用召回后 rerank 精排（需 ANTHROPIC_AUTH_TOKEN）"
    )
    ZHIPU_RERANK_MODEL: str = Field(
        default="rerank", description="智谱 Rerank 模型编码"
    )
    RETRIEVAL_TOP_K: int = Field(
        default=20, ge=1, le=128, description="向量召回候选数（rerank 前，API 上限 128）"
    )
    RERANK_TOP_K: int = Field(
        default=5, ge=1, description="rerank 精排后保留的结果数"
    )
    RERANK_SCORE_THRESHOLD: float = Field(
        default=0.0, ge=0.0, le=1.0, description="rerank 相关性分数阈值，低于阈值的片段被过滤"
    )

    # ---- SQLite 沙箱（SQL Query 工具） ----
    SQLITE_SANDBOX_PATH: str = Field(
        default="", description="SQLite 沙箱路径，留空则用 data/sandbox.db"
    )

    # ---- Memory 配置 ----
    ENABLE_LONG_TERM_MEMORY: bool = Field(
        default=False, description="是否启用长期记忆（需配置 ANTHROPIC_AUTH_TOKEN）"
    )

    # ---- LangGraph Checkpoint 配置 ----
    # 启用后每个任务（thread_id=task_id）的执行状态写入检查点，支持崩溃恢复/断点续跑。
    ENABLE_CHECKPOINTING: bool = Field(
        default=True, description="是否启用 LangGraph Checkpoint 持久化"
    )
    # auto: 优先 PostgreSQL（PostgresSaver），不可用则降级内存 MemorySaver；
    # postgres: 强制 PostgresSaver；memory: 强制进程内 MemorySaver。
    CHECKPOINT_BACKEND: str = Field(
        default="auto", description="Checkpoint 后端：auto | postgres | memory"
    )

    # ---- Agent 配置 ----
    MAX_REPLAN_ITERATIONS: int = Field(
        default=3, ge=1, description="最大重新规划次数，防止无限循环"
    )
    MAX_EXECUTION_STEPS: int = Field(
        default=10, ge=1, description="单任务最大执行步骤，防止无限循环"
    )
    MAX_AGENT_STEPS: int = Field(
        default=10, ge=1, description="单任务最大 Agent 执行轮数（多 Agent 协作上限），防止无限循环"
    )
    SUB_AGENT_TIMEOUT_SECONDS: float = Field(
        default=60.0,
        ge=1,
        description=(
            "单个子 Agent / Reviewer 的 LLM 调用超时（秒），"
            "防止外部模型挂起导致系统无限等待"
        ),
    )

    # ---- 工具审批（HITL）策略 ----
    # 触发人工审批的最低工具风险等级：L2（默认，仅高风险）/ L1（有业务影响）/ L0（全部）。
    # 只读、无副作用的工具（web_search 等）默认 L0，不触发 HITL。
    TOOL_APPROVAL_LEVEL: str = Field(
        default="L2",
        description=(
            "触发人工审批（HITL）的最低工具风险等级：L2（默认，仅高风险）/ "
            "L1（有业务影响）/ L0（全部工具）"
        ),
    )
    # 显式覆盖：强制要求审批的工具名列表（优先级高于风险分级），空列表表示不覆盖。
    TOOL_APPROVAL_OVERRIDE_TOOLS: list[str] = Field(
        default=[], description="强制要求人工审批的工具名列表（覆盖风险分级）"
    )

    # ---- LLM 成本控制（单任务预算） ----
    # 0 表示不限制。超限时任务终止并标记 FAILED（BudgetExceededError）。
    MAX_LLM_CALLS_PER_TASK: int = Field(
        default=20, ge=0, description="单任务最大 LLM 调用次数（0=不限）"
    )
    MAX_TOTAL_TOKENS_PER_TASK: int = Field(
        default=50000, ge=0, description="单任务最大 token 消耗（prompt+completion，0=不限）"
    )
    BUDGET_LIMIT_USD: float = Field(
        default=0.0, ge=0.0, description="单任务成本上限（美元，0=不限）"
    )
    LLM_INPUT_COST_PER_1M: float = Field(
        default=0.0, ge=0.0,
        description="每百万输入 token 价格（美元），用于成本估算与预算核算",
    )
    LLM_OUTPUT_COST_PER_1M: float = Field(
        default=0.0, ge=0.0,
        description="每百万输出 token 价格（美元），用于成本估算与预算核算",
    )

    # ---- 任务队列（异步化：API 入队，Worker 消费） ----
    # auto: 优先 Redis（可靠持久队列），不可用则降级进程内内存队列；
    # redis: 强制 Redis；memory: 强制内存队列（单进程开发/测试）。
    TASK_QUEUE_BACKEND: str = Field(
        default="auto", description="任务队列后端：auto | redis | memory"
    )
    TASK_QUEUE_EMBEDDED_WORKER: bool = Field(
        default=True,
        description=(
            "是否在应用进程内启动内置 Worker 消费任务队列。"
            "多进程/生产部署（独立 python -m app.worker）时设为 false"
        ),
    )
    REDIS_QUEUE_KEY: str = Field(
        default="agent:tasks:queue", description="Redis 任务队列键名"
    )
    QUEUE_DEQUEUE_TIMEOUT: float = Field(
        default=1.0, ge=0.1, le=30, description="Worker 拉取任务超时（秒）"
    )

    # ---- 计算属性 ----
    @property
    def postgres_dsn(self) -> str:
        """同步 SQLAlchemy DSN"""
        return (
            f"postgresql://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @property
    def postgres_async_dsn(self) -> str:
        """异步 asyncpg DSN"""
        return (
            f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @property
    def redis_url(self) -> str:
        """Redis 连接 URL"""
        return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"

    @property
    def chroma_dir(self) -> str:
        """Chroma 持久化目录（绝对路径）"""
        if self.CHROMA_PERSIST_DIR:
            return self.CHROMA_PERSIST_DIR
        return str(BASE_DIR / "data" / "chroma")

    @property
    def sqlite_sandbox_path(self) -> str:
        """SQLite 沙箱库路径（绝对路径）"""
        if self.SQLITE_SANDBOX_PATH:
            return self.SQLITE_SANDBOX_PATH
        return str(BASE_DIR / "data" / "sandbox.db")

    @property
    def task_db_path(self) -> str:
        """sqlite 任务库路径（绝对路径）"""
        if self.TASK_DB_PATH:
            return self.TASK_DB_PATH
        return str(BASE_DIR / "data" / "tasks.db")

    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    """获取全局配置单例（缓存）。"""
    return Settings()
