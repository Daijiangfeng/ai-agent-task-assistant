"""
任务队列与 Worker 测试。
覆盖内存队列语义、工厂降级、QueueWorker 消费（含异常兜底）与
TaskMessage 序列化。
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.settings import Settings
from app.queue.base import QueueFullError, TaskMessage
from app.queue.factory import create_task_queue
from app.queue.memory_queue import InMemoryTaskQueue
from app.queue.redis_queue import RedisTaskQueue
from app.worker import QueueWorker


class TestTaskMessage:
    """消息载荷序列化。"""

    def test_roundtrip(self):
        """to_dict / from_dict 往返保真（含调用者身份）。"""
        msg = TaskMessage(
            task_id="t-1",
            goal="目标",
            context="上下文",
            tool_context={"user_id": "alice", "tenant_id": "ten-a", "role": "user"},
        )
        restored = TaskMessage.from_dict(msg.to_dict())
        assert restored.task_id == "t-1"
        assert restored.goal == "目标"
        assert restored.context == "上下文"
        assert restored.tool_context == msg.tool_context

    def test_to_tool_context_defaults_admin(self):
        """无身份时还原为 admin 内部调用。"""
        msg = TaskMessage(task_id="t-2", goal="g")
        ctx = msg.to_tool_context()
        assert ctx.user_id == "anonymous"


class TestInMemoryTaskQueue:
    """内存队列语义。"""

    @pytest.mark.asyncio
    async def test_fifo_roundtrip(self):
        """先入先出，出队返回消息本身。"""
        queue = InMemoryTaskQueue()
        await queue.enqueue(TaskMessage(task_id="a", goal="A"))
        await queue.enqueue(TaskMessage(task_id="b", goal="B"))
        assert await queue.size() == 2
        assert (await queue.dequeue(timeout=0.1)).task_id == "a"
        assert (await queue.dequeue(timeout=0.1)).task_id == "b"
        assert await queue.size() == 0

    @pytest.mark.asyncio
    async def test_dequeue_timeout_returns_none(self):
        """空队列超时返回 None（Worker 空闲轮询不空转阻塞）。"""
        queue = InMemoryTaskQueue()
        assert await queue.dequeue(timeout=0.05) is None

    @pytest.mark.asyncio
    async def test_full_queue_raises(self):
        """队列满时入队抛 QueueFullError。"""
        queue = InMemoryTaskQueue(maxsize=1)
        await queue.enqueue(TaskMessage(task_id="a", goal="A"))
        with pytest.raises(QueueFullError):
            await queue.enqueue(TaskMessage(task_id="b", goal="B"))


class TestQueueFactory:
    """队列工厂选择逻辑。"""

    def test_memory_backend_forced(self):
        """memory 后端直接返回内存队列。"""
        queue = create_task_queue(Settings(TASK_QUEUE_BACKEND="memory"))
        assert isinstance(queue, InMemoryTaskQueue)

    def test_auto_falls_back_to_memory(self, monkeypatch):
        """auto 模式 Redis 探测失败时降级内存队列（离线可跑）。"""
        monkeypatch.setattr(RedisTaskQueue, "ping", AsyncMock(return_value=False))
        queue = create_task_queue(Settings(TASK_QUEUE_BACKEND="auto"))
        assert isinstance(queue, InMemoryTaskQueue)

    def test_auto_uses_redis_when_reachable(self, monkeypatch):
        """auto 模式 Redis 可达时返回 Redis 队列。"""
        monkeypatch.setattr(RedisTaskQueue, "ping", AsyncMock(return_value=True))
        queue = create_task_queue(Settings(TASK_QUEUE_BACKEND="auto"))
        assert isinstance(queue, RedisTaskQueue)


class TestQueueWorker:
    """Worker 消费逻辑。"""

    @pytest.mark.asyncio
    async def test_process_one_executes_task(self):
        """process_one 从队列取消息并交给 AgentService 执行（还原调用者身份）。"""
        from app.tools.security import ToolContext

        queue = InMemoryTaskQueue()
        agent_service = MagicMock()
        agent_service.run_task = AsyncMock(return_value="ok")
        worker = QueueWorker(queue, agent_service, Settings())

        await queue.enqueue(
            TaskMessage(
                task_id="t-1",
                goal="目标",
                context="上下文",
                tool_context={"user_id": "alice", "tenant_id": "ten-a", "role": "user"},
            )
        )
        processed = await worker.process_one(timeout=0.5)
        assert processed is True
        agent_service.run_task.assert_awaited_once()
        kwargs = agent_service.run_task.await_args.kwargs
        assert kwargs["task_id"] == "t-1"
        assert kwargs["goal"] == "目标"
        assert kwargs["context"] == "上下文"
        ctx = kwargs["tool_context"]
        assert isinstance(ctx, ToolContext)
        assert ctx.user_id == "alice"
        assert ctx.tenant_id == "ten-a"

    @pytest.mark.asyncio
    async def test_process_one_empty_queue(self):
        """空队列 process_one 返回 False。"""
        worker = QueueWorker(InMemoryTaskQueue(), MagicMock(), Settings())
        assert await worker.process_one(timeout=0.05) is False

    @pytest.mark.asyncio
    async def test_execute_swallows_errors(self):
        """单任务异常被兜底（AgentService 已标记 FAILED），Worker 不崩溃。"""
        queue = InMemoryTaskQueue()
        agent_service = MagicMock()
        agent_service.run_task = AsyncMock(side_effect=RuntimeError("boom"))
        worker = QueueWorker(queue, agent_service, Settings())
        await queue.enqueue(TaskMessage(task_id="t-1", goal="g"))
        assert await worker.process_one(timeout=0.5) is True

    @pytest.mark.asyncio
    async def test_run_loop_until_stop(self):
        """run 循环持续消费，stop 后优雅退出。"""
        queue = InMemoryTaskQueue()
        agent_service = MagicMock()
        agent_service.run_task = AsyncMock(return_value="ok")
        worker = QueueWorker(queue, agent_service, Settings())

        await queue.enqueue(TaskMessage(task_id="t-1", goal="g1"))
        await queue.enqueue(TaskMessage(task_id="t-2", goal="g2"))

        import asyncio

        task = asyncio.create_task(worker.run())
        # 等待两条消息被消费后停止
        for _ in range(100):
            await asyncio.sleep(0.05)
            if agent_service.run_task.await_count >= 2:
                break
        worker.stop()
        await asyncio.wait_for(task, timeout=3.0)

        assert agent_service.run_task.await_count == 2
        assert await queue.size() == 0

    @pytest.mark.asyncio
    async def test_embedded_worker_start_stop(self):
        """EmbeddedWorker 随应用启停，重复 start 幂等。"""
        from app.worker import EmbeddedWorker

        queue = InMemoryTaskQueue()
        agent_service = MagicMock()
        agent_service.run_task = AsyncMock(return_value="ok")
        embedded = EmbeddedWorker(queue, agent_service, Settings())
        embedded.start()
        embedded.start()  # 幂等
        # 让 Worker 有机会消费消息后再停止（避免 stop 先于首次轮询）
        import asyncio

        await queue.enqueue(TaskMessage(task_id="t-1", goal="g"))
        await asyncio.sleep(0.3)
        await embedded.stop()
        assert agent_service.run_task.await_count == 1
