"""
任务队列抽象层。

职责：解耦 API 与 Agent 执行——API 侧 enqueue 后立即返回，
Worker（app/worker.py 或应用内嵌 Worker）dequeue 消费并执行。
后端可插拔：Redis（生产，跨进程可靠持久队列）或内存（单进程开发/测试）。

消息携带执行所需的全部输入（含调用者身份），Worker 不依赖请求上下文。
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from app.tools.security import ToolContext


@dataclass
class TaskMessage:
    """任务执行消息（入队/出队的载荷）。"""

    task_id: str
    goal: str = ""
    context: str | None = None
    tool_context: dict | None = None
    # 动作类型：
    # - execute: 正常执行任务（含断点续跑）；
    # - approval_resume: 审批决策后恢复执行（payload 为审批决策）。
    action: str = "execute"
    payload: dict | None = None
    enqueued_at: float = field(default_factory=time.time)

    def to_tool_context(self) -> ToolContext:
        """还原调用者身份上下文（缺省视为内部调用）。"""
        if self.tool_context:
            return ToolContext.from_dict(self.tool_context)
        from app.tools.security import ROLE_ADMIN

        return ToolContext(role=ROLE_ADMIN)

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "goal": self.goal,
            "context": self.context,
            "tool_context": self.tool_context,
            "action": self.action,
            "payload": self.payload,
            "enqueued_at": self.enqueued_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TaskMessage":
        return cls(
            task_id=data["task_id"],
            goal=data.get("goal", ""),
            context=data.get("context"),
            tool_context=data.get("tool_context"),
            action=data.get("action", "execute"),
            payload=data.get("payload"),
            enqueued_at=data.get("enqueued_at", 0.0),
        )


class TaskQueue(ABC):
    """
    任务队列抽象接口。

    - enqueue: 入队（非阻塞，队列满时抛 QueueFullError）；
    - dequeue: 出队（阻塞至多 timeout 秒，超时返回 None）；
    - size: 当前积压数量（监控用）。
    """

    @abstractmethod
    async def enqueue(self, message: TaskMessage) -> None:
        """将任务消息入队。"""
        ...

    @abstractmethod
    async def dequeue(self, timeout: float = 1.0) -> TaskMessage | None:
        """阻塞出队一条消息；超时返回 None。"""
        ...

    @abstractmethod
    async def close(self) -> None:
        """关闭队列，释放底层连接。"""
        ...

    @abstractmethod
    async def size(self) -> int:
        """当前队列积压数量。"""
        ...


class QueueFullError(RuntimeError):
    """队列已满，入队失败。"""
