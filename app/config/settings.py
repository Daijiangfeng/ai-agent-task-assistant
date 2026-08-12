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

    # ---- Redis ----
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0

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

    # ---- Agent 配置 ----
    MAX_REPLAN_ITERATIONS: int = Field(
        default=3, ge=1, description="最大重新规划次数，防止无限循环"
    )
    MAX_EXECUTION_STEPS: int = Field(
        default=10, ge=1, description="单任务最大执行步骤，防止无限循环"
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
