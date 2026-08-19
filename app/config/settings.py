"""
应用配置管理模块。
使用 pydantic-settings 实现分层配置，支持 .env 文件和环境变量覆盖。
"""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# 项目根目录（ai-agent-task-assistant/）
BASE_DIR = Path(__file__).resolve().parent.parent.parent

ENVIRONMENT_DEVELOPMENT = "development"
ENVIRONMENT_PRODUCTION = "production"


class Settings(BaseSettings):
    """应用全局配置，所有配置项均可通过环境变量或 .env 文件覆盖。"""

    # ---- 应用配置 ----
    APP_NAME: str = "AI Agent Task Assistant"
    APP_VERSION: str = "0.1.0"
    DEBUG: bool = False

    # ---- 运行环境（P0-1 生产安全模式） ----
    ENVIRONMENT: str = Field(
        default=ENVIRONMENT_DEVELOPMENT,
        description=(
            "运行环境：development | production。production 下禁止一切静默降级"
            "（内存任务存储 / MemorySaver / 内存队列 / 内存短期记忆 / Mock 工具），"
            "核心基础设施不可用时启动失败或拒绝接收任务。"
        ),
    )

    @field_validator("ENVIRONMENT", mode="before")
    @classmethod
    def _normalize_environment(cls, value: str) -> str:
        """规范化运行环境取值（大小写不敏感），非法值直接报配置错误。"""
        normalized = str(value or "").strip().lower()
        if normalized not in (ENVIRONMENT_DEVELOPMENT, ENVIRONMENT_PRODUCTION):
            raise ValueError(
                "ENVIRONMENT 仅支持 development | production，"
                f"收到: {value!r}"
            )
        return normalized

    @property
    def is_production(self) -> bool:
        """是否生产环境（禁止静默降级、启动即校验基础设施）。"""
        return self.ENVIRONMENT == ENVIRONMENT_PRODUCTION

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

    # ---- Chroma 向量库（长期记忆后端存储） ----
    CHROMA_PERSIST_DIR: str = Field(
        default="", description="Chroma 持久化目录，留空则用 data/chroma"
    )

    # ---- 向量库后端（可插拔，长期记忆使用） ----
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

    # ---- 健康检查（P0-1 生产安全模式） ----
    HEALTH_CHECK_TIMEOUT: float = Field(
        default=2.0,
        ge=0.1,
        le=30,
        description="基础设施健康检查中单个组件的探测超时（秒）",
    )
    HEALTH_CACHE_TTL: float = Field(
        default=5.0,
        ge=0.0,
        description="就绪状态缓存 TTL（秒）；0 表示每次实时探测",
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
