"""
API 路由汇总模块。
将 v1 版本的所有路由聚合到统一入口。
"""

from fastapi import APIRouter

from app.api.v1.agent import router as agent_router
from app.api.v1.knowledge import router as knowledge_router
from app.api.v1.tasks import router as tasks_router

api_router = APIRouter(prefix="/api/v1")

api_router.include_router(tasks_router)
api_router.include_router(agent_router)
api_router.include_router(knowledge_router)
