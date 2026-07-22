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

    # ---- 智谱 LLM 配置 ----
    ZHIPU_API_KEY: str = Field(default="", description="智谱 GLM API Key")
    ZHIPU_BASE_URL: str = Field(
        default="https://open.bigmodel.cn/api/paas/v4/",
        description="智谱 OpenAI Compatible API 基础地址",
    )
    ZHIPU_MODEL: str = Field(default="glm-4-plus", description="默认模型名称")
    ZHIPU_TEMPERATURE: float = Field(default=0.7, ge=0.0, le=2.0)
    ZHIPU_MAX_TOKENS: int = Field(default=4096, ge=1)
    ZHIPU_EMBEDDING_MODEL: str = Field(
        default="embedding-3", description="智谱 Embedding 模型名称"
    )

    # ---- PostgreSQL ----
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_DB: str = "agent_db"
    POSTGRES_USER: str = "postgres"
    POSTGRES_PASSWORD: str = ""

    # ---- Redis ----
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0

    # ---- RabbitMQ ----
    RABBITMQ_HOST: str = "localhost"
    RABBITMQ_PORT: int = 5672
    RABBITMQ_USER: str = "guest"
    RABBITMQ_PASSWORD: str = "guest"

    # ---- Milvus ----
    MILVUS_HOST: str = "localhost"
    MILVUS_PORT: int = 19530

    # ---- Web Search (Tavily) ----
    TAVILY_API_KEY: str = Field(default="", description="Tavily 搜索 API Key")
    WEB_SEARCH_MAX_RESULTS: int = Field(default=5, ge=1, le=20)

    # ---- Chroma 向量库 ----
    CHROMA_PERSIST_DIR: str = Field(
        default="", description="Chroma 持久化目录，留空则用 data/chroma"
    )

    # ---- RAG 配置 ----
    RAG_CHUNK_SIZE: int = Field(default=800, ge=100)
    RAG_CHUNK_OVERLAP: int = Field(default=100, ge=0)
    RAG_TOP_K: int = Field(default=5, ge=1, le=50)

    # ---- SQLite 沙箱（SQL Query 工具） ----
    SQLITE_SANDBOX_PATH: str = Field(
        default="", description="SQLite 沙箱路径，留空则用 data/sandbox.db"
    )

    # ---- Memory 配置 ----
    ENABLE_LONG_TERM_MEMORY: bool = Field(
        default=False, description="是否启用长期记忆（需智谱 API Key）"
    )

    # ---- Agent 配置 ----
    MAX_REPLAN_ITERATIONS: int = Field(
        default=3, ge=1, description="最大重新规划次数，防止无限循环"
    )
    MAX_EXECUTION_STEPS: int = Field(
        default=10, ge=1, description="单任务最大执行步骤数"
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
    def rabbitmq_url(self) -> str:
        """RabbitMQ 连接 URL"""
        return (
            f"amqp://{self.RABBITMQ_USER}:{self.RABBITMQ_PASSWORD}"
            f"@{self.RABBITMQ_HOST}:{self.RABBITMQ_PORT}//"
        )

    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )


@lru_cache()
def get_settings() -> Settings:
    """获取全局配置单例（缓存）。"""
    return Settings()
