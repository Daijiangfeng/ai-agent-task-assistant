"""
FastAPI 依赖注入模块。
提供全局单例依赖：Settings、TaskService、AgentService、RAGService、Memory。
"""

from app.config.settings import get_settings
from app.memory.base import BaseMemory
from app.memory.factory import MemoryFactory
from app.rag.service import RAGService
from app.services.agent_service import AgentService
from app.services.task_service import TaskService

# 全局单例服务实例
_task_service: TaskService | None = None
_agent_service: AgentService | None = None
_rag_service: RAGService | None = None
_short_term_memory: BaseMemory | None = None
_long_term_memory: BaseMemory | None = None


def get_task_service() -> TaskService:
    """获取 TaskService 单例。"""
    global _task_service
    if _task_service is None:
        _task_service = TaskService()
    return _task_service


def get_agent_service() -> AgentService:
    """获取 AgentService 单例。"""
    global _agent_service
    if _agent_service is None:
        _agent_service = AgentService(task_service=get_task_service())
    return _agent_service


def get_rag_service() -> RAGService:
    """获取 RAGService 单例。"""
    global _rag_service
    if _rag_service is None:
        _rag_service = RAGService(get_settings())
    return _rag_service


def get_short_term_memory() -> BaseMemory:
    """获取短期记忆单例（Redis + 内存降级）。"""
    global _short_term_memory
    if _short_term_memory is None:
        _short_term_memory = MemoryFactory.create_short_term(get_settings())
    return _short_term_memory


def get_long_term_memory() -> BaseMemory:
    """获取长期记忆单例（Chroma 向量库）。"""
    global _long_term_memory
    if _long_term_memory is None:
        _long_term_memory = MemoryFactory.create_long_term(get_settings())
    return _long_term_memory
