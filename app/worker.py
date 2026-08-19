"""
Agent Worker：任务队列消费者。

消费任务队列中的 TaskMessage，调用 AgentService 执行任务，实现
"API 快速入队返回，Worker 异步执行"的解耦架构。

两种运行方式：
1. 独立进程（生产推荐）：python -m app.worker
   - 可与 API 进程分离部署、水平扩展（多实例并发消费 Redis 队列）；
   - 通过 SIGINT/SIGTERM 优雅退出（等待当前任务完成）。
2. 应用内嵌（默认）：TASK_QUEUE_EMBEDDED_WORKER=true 时由
   app/main.py lifespan 启动本模块的 EmbeddedWorker（单进程开发/测试）。

任务执行的可靠性由 LangGraph Checkpoint（thread_id=task_id）保证：
Worker 崩溃后任务可从检查点断点续跑，出队即执行、无需 ACK。
"""

from __future__ import annotations

import asyncio
import signal
from typing import Any

from app.config.logging import get_logger, setup_logging
from app.config.settings import Settings, get_settings
from app.queue.base import TaskMessage, TaskQueue
from app.services.agent_service import AgentService
from app.services.task_service import TaskService

logger = get_logger(__name__)


class QueueWorker:
    """
    队列消费 Worker。

    循环：dequeue -> AgentService.run_task -> 继续。
    支持 process_one（测试/运维单步处理）与 run（无限循环）。
    """

    def __init__(
        self,
        queue: TaskQueue,
        agent_service: AgentService,
        settings: Settings | None = None,
    ):
        self._queue = queue
        self._agent_service = agent_service
        self._settings = settings or get_settings()
        self._stop = asyncio.Event()

    def stop(self) -> None:
        """请求优雅停止（run 循环在下一次轮询时退出）。"""
        self._stop.set()

    async def process_one(self, timeout: float | None = None) -> bool:
        """
        处理一条消息（无消息返回 False）。

        Args:
            timeout: 出队等待时间；None 使用 settings.QUEUE_DEQUEUE_TIMEOUT。

        Returns:
            是否处理了消息。
        """
        msg = await self._queue.dequeue(
            timeout=timeout or self._settings.QUEUE_DEQUEUE_TIMEOUT
        )
        if msg is None:
            return False
        await self._execute(msg)
        return True

    async def _execute(self, msg: TaskMessage) -> None:
        """执行单条任务消息（异常不中断 Worker 主循环）。"""
        try:
            if msg.action == "approval_resume":
                # 审批决策后的恢复执行：以 Command(resume=决策) 续跑被暂停的 Workflow
                await self._agent_service.resume_approval(
                    task_id=msg.task_id,
                    decision=msg.payload or {},
                    tool_context=msg.to_tool_context(),
                )
                return

            retry_from_index = None
            if msg.payload:
                retry_from_index = msg.payload.get("retry_from_index")

            await self._agent_service.run_task(
                task_id=msg.task_id,
                goal=msg.goal,
                context=msg.context,
                tool_context=msg.to_tool_context(),
                retry_from_index=retry_from_index,
            )
        except Exception as e:
            # 兜底：任务级异常已在 AgentService 内部处理（标记 FAILED），
            # 此处仅防御 Worker 代码自身的意外异常。
            logger.error("Worker: 任务处理异常", task_id=msg.task_id, error=str(e))

    async def run(self) -> None:
        """持续消费队列，直到 stop() 被调用。"""
        logger.info("Worker: 开始消费任务队列", backend=type(self._queue).__name__)
        while not self._stop.is_set():
            try:
                await self.process_one()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # pragma: no cover - 防御性兜底
                logger.error("Worker: 消费循环异常", error=str(e))
                await asyncio.sleep(1.0)
        logger.info("Worker: 停止消费任务队列")


class EmbeddedWorker:
    """
    应用内嵌 Worker（单进程模式）。

    在 FastAPI lifespan 中以 asyncio task 运行，随应用启停。
    """

    def __init__(
        self,
        queue: TaskQueue,
        agent_service: AgentService,
        settings: Settings | None = None,
    ):
        self._inner = QueueWorker(queue, agent_service, settings)
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        """启动后台消费任务（幂等）。"""
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._inner.run())

    async def stop(self) -> None:
        """停止消费并等待退出。"""
        if self._task is None:
            return
        self._inner.stop()
        try:
            await asyncio.wait_for(self._task, timeout=5.0)
        except asyncio.TimeoutError:  # pragma: no cover - 当前任务超长时取消
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        self._task = None


def build_worker_deps(settings: Settings | None = None) -> dict[str, Any]:
    """
    构造 Worker 依赖（与 API 进程一致的配置来源，避免依赖请求上下文）。

    Returns:
        {"queue": TaskQueue, "agent_service": AgentService}
    """
    settings = settings or get_settings()
    from app.memory.factory import MemoryFactory
    from app.queue.factory import create_task_queue

    task_service = TaskService(settings)
    long_term_memory = None
    if settings.ENABLE_LONG_TERM_MEMORY:
        long_term_memory = MemoryFactory.create_long_term(settings)
    agent_service = AgentService(
        task_service=task_service,
        settings=settings,
        long_term_memory=long_term_memory,
    )
    queue = create_task_queue(settings)
    return {"queue": queue, "agent_service": agent_service}


async def _run_forever(worker: QueueWorker) -> None:
    """注册信号处理器并运行 Worker（仅独立进程模式）。"""

    def _request_stop(signum, frame):  # pragma: no cover - 信号回调
        logger.info("Worker: 收到停止信号", signal=signum)
        worker.stop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _request_stop)
        except (ValueError, OSError):  # pragma: no cover - 非主线程环境
            pass
    await worker.run()


def run_worker(settings: Settings | None = None) -> None:
    """独立 Worker 进程入口（python -m app.worker）。"""
    settings = settings or get_settings()
    setup_logging(debug=settings.DEBUG)
    deps = build_worker_deps(settings)
    worker = QueueWorker(deps["queue"], deps["agent_service"], settings)
    try:
        asyncio.run(_run_forever(worker))
    finally:
        asyncio.run(deps["queue"].close())


if __name__ == "__main__":
    run_worker()
