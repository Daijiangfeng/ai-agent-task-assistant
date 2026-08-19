"""
API 路由汇总模块。
将 v1 版本的所有路由聚合到统一入口。
"""

from fastapi import APIRouter

from app.api.health import router as health_router
from app.api.v1.agent import router as agent_router
from app.api.v1.stats import router as stats_router
from app.api.v1.tasks import router as tasks_router
from app.api.v1.templates import router as templates_router
from app.api.v1.tools import router as tools_router
from app.api.v1.traces import router as traces_router

api_router = APIRouter(prefix="/api/v1")

api_router.include_router(health_router)
api_router.include_router(tasks_router)
api_router.include_router(agent_router)
api_router.include_router(tools_router)
api_router.include_router(stats_router)
api_router.include_router(traces_router)
api_router.include_router(templates_router)
