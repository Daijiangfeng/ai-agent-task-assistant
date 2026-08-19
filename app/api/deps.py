"""
FastAPI 依赖注入模块。
提供全局单例依赖：Settings、TaskService、AgentService、RAGService、Memory、任务队列。
"""

from app.agent.approval import HumanApprovalGate
from app.config.settings import get_settings
from app.memory.base import BaseMemory
from app.memory.factory import MemoryFactory
from app.queue.base import TaskQueue
from app.queue.factory import create_task_queue
from app.rag.service import RAGService
from app.services.agent_service import AgentService
from app.services.task_service import TaskService
from app.services.template_service import TemplateService, get_template_service

# 全局单例服务实例
_task_service: TaskService | None = None
_agent_service: AgentService | None = None
_rag_service: RAGService | None = None
_short_term_memory: BaseMemory | None = None
_long_term_memory: BaseMemory | None = None
_task_queue: TaskQueue | None = None


def get_task_service() -> TaskService:
    """获取 TaskService 单例。"""
    global _task_service
    if _task_service is None:
        _task_service = TaskService()
    return _task_service


def get_agent_service() -> AgentService:
    """获取 AgentService 单例（默认注入 Human-in-the-loop 审批闸门）。"""
    global _agent_service
    if _agent_service is None:
        _agent_service = AgentService(
            task_service=get_task_service(),
            approval_gate=HumanApprovalGate(),
        )
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


def get_task_queue() -> TaskQueue:
    """获取任务队列单例（API 入队 / Worker 消费共用）。"""
    global _task_queue
    if _task_queue is None:
        _task_queue = create_task_queue(get_settings())
    return _task_queue


def reset_task_queue() -> None:
    """重置任务队列单例（测试隔离用）。"""
    global _task_queue
    _task_queue = None


def get_template_service_dep() -> TemplateService:
    """获取任务模板服务单例（FastAPI 依赖包装）。"""
    return get_template_service()
