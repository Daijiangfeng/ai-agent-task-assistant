"""
健康检查 API 路由。

- GET /health：向后兼容的应用状态端点（保持原有行为不变）；
- GET /health/live：存活探针（Liveness），进程存活即返回 200，
  不依赖外部基础设施，供编排系统判断是否需要重启容器；
- GET /health/ready：就绪探针（Readiness），核心基础设施可用才返回 200，
  不可用返回 503 且 ready=false，供编排系统摘除流量。

本路由同时挂载于根路径与 /api/v1 前缀下。
"""

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.config.settings import get_settings
from app.models.api_schemas import (
    HealthResponse,
    LivenessResponse,
    ReadinessResponse,
)
from app.services.health import get_readiness_gate

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health_check():
    """健康检查接口。"""
    settings = get_settings()
    return HealthResponse(status="ok", version=settings.APP_VERSION)


@router.get("/health/live", response_model=LivenessResponse)
async def liveness_check():
    """存活探针：进程存活即返回（不探测外部依赖，避免基础设施抖动引发重启）。"""
    settings = get_settings()
    return LivenessResponse(status="alive", version=settings.APP_VERSION)


@router.get("/health/ready", response_model=ReadinessResponse)
async def readiness_check():
    """就绪探针：核心基础设施不可用时 ready=false 并返回 503。"""
    settings = get_settings()
    report = await get_readiness_gate().get_report()
    body = ReadinessResponse(
        status="ready" if report.ready else "not_ready",
        ready=report.ready,
        environment=settings.ENVIRONMENT,
        version=settings.APP_VERSION,
        components=[
            # 过滤内部字段：core 已包含在组件 schema 中
            {"name": c.name, "status": c.status, "detail": c.detail,
             "latency_ms": c.latency_ms, "core": c.core}
            for c in report.components
        ],
    )
    if not report.ready:
        return JSONResponse(status_code=503, content=body.model_dump())
    return body
