"""
AI Agent Task Assistant - FastAPI 入口。
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.errors import AppException, ErrorHandlerMiddleware
from app.api.router import api_router
from app.config.logging import get_logger, setup_logging
from app.config.settings import get_settings
from app.models.api_schemas import HealthResponse
from app.prompts.manager import PromptManager

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理。"""
    # 启动时初始化
    settings = get_settings()
    setup_logging(debug=settings.DEBUG)
    logger.info("应用启动中...", app_name=settings.APP_NAME)

    # 初始化 Prompt 管理器
    PromptManager.init_defaults()
    logger.info("Prompt 模板注册完成")

    # 初始化数据目录（Chroma 持久化、SQLite 沙箱）
    from pathlib import Path

    Path(settings.chroma_dir).mkdir(parents=True, exist_ok=True)
    Path(settings.sqlite_sandbox_path).parent.mkdir(parents=True, exist_ok=True)
    logger.info("数据目录初始化完成", chroma_dir=settings.chroma_dir)

    # 注册内置工具
    from app.tools.builtins import register_builtin_tools

    register_builtin_tools()
    logger.info("内置工具注册完成")

    yield

    # 关闭时清理
    logger.info("应用关闭中...")


def create_app() -> FastAPI:
    """
    FastAPI 应用工厂函数。

    Returns:
        配置完成的 FastAPI 应用实例。
    """
    settings = get_settings()

    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        description=(
            "企业级 AI Agent 任务执行助手，"
            "具备自主规划、工具调用、知识增强和自我反思能力。"
        ),
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )

    # 全局异常处理中间件
    app.add_middleware(ErrorHandlerMiddleware)

    # CORS 中间件
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 自定义异常处理器
    @app.exception_handler(AppException)
    async def app_exception_handler(request: Request, exc: AppException):
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.message},
        )

    # 注册 API 路由
    app.include_router(api_router)

    # 健康检查
    @app.get("/health", response_model=HealthResponse, tags=["health"])
    async def health_check():
        """健康检查接口。"""
        return HealthResponse(status="ok", version=settings.APP_VERSION)

    return app


app = create_app()
