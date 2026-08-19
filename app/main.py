"""
AI Agent Task Assistant - FastAPI 应用工厂。

应用实例的构建、生命周期管理（lifespan）、中间件装配与路由注册集中于此。
仓库根目录的 main.py 作为薄入口，透传 app 对象以兼容
`uvicorn main:app` 与 `from main import app` 两种引用方式。
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.errors import AppException, ErrorHandlerMiddleware
from app.api.health import router as health_router
from app.api.router import api_router
from app.config.logging import get_logger, setup_logging
from app.config.settings import get_settings
from app.prompts.manager import PromptManager

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理。"""
    # 启动时初始化
    settings = get_settings()
    setup_logging(debug=settings.DEBUG)
    logger.info("应用启动中...", app_name=settings.APP_NAME)
    # 输出当前生效模型，方便排查配置问题
    logger.info(f"Current LLM Model: {settings.ZHIPU_MODEL}")

    # 初始化 Prompt 管理器
    PromptManager.init_defaults()
    logger.info("Prompt 模板注册完成")

    # 初始化数据目录（Chroma 持久化、SQLite 沙箱）
    from pathlib import Path

    Path(settings.chroma_dir).mkdir(parents=True, exist_ok=True)
    Path(settings.sqlite_sandbox_path).parent.mkdir(parents=True, exist_ok=True)
    logger.info("数据目录初始化完成", chroma_dir=settings.chroma_dir)

    # 生产模式：启动即校验基础设施（PostgreSQL/Redis/向量库/LLM/存储），
    # 核心依赖不可用直接终止启动（禁止静默降级，fail-fast）
    if settings.is_production:
        from app.services.health import verify_production_readiness

        report = await verify_production_readiness(settings)
        logger.info(
            "生产环境基础设施健康检查通过",
            environment=settings.ENVIRONMENT,
            components=[c.name for c in report.components],
        )

    # 注册内置工具
    from app.tools.builtins import register_builtin_tools

    register_builtin_tools()
    logger.info("内置工具注册完成")

    # 内嵌 Worker：TASK_QUEUE_EMBEDDED_WORKER=true 时在应用进程内消费任务队列
    # （单进程开发/测试模式；生产多进程部署请设 false 并独立运行 python -m app.worker）
    embedded_worker = None
    if settings.TASK_QUEUE_EMBEDDED_WORKER:
        from app.api.deps import get_agent_service, get_task_queue
        from app.worker import EmbeddedWorker

        embedded_worker = EmbeddedWorker(
            queue=get_task_queue(),
            agent_service=get_agent_service(),
            settings=settings,
        )
        embedded_worker.start()
        logger.info("内嵌 Worker 已启动")

    yield

    # 关闭时清理
    logger.info("应用关闭中...")
    if embedded_worker is not None:
        await embedded_worker.stop()
        logger.info("内嵌 Worker 已停止")

    from app.agent.checkpoint import close_all_checkpoint_pools

    await close_all_checkpoint_pools()
    logger.info("Checkpoint 连接池已释放")


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
    # 注意：allow_credentials=True 时 allow_origins 不能为 "*"（Fetch 规范禁止），
    # 否则浏览器会拒绝带凭证的跨域请求。此处使用显式来源白名单。
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
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

    # 注册 API 路由（api_router 带 /api/v1 前缀；health_router 挂在根路径 /health）
    app.include_router(api_router)
    app.include_router(health_router)

    return app


app = create_app()
