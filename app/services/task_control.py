"""
任务控制服务（暂停 / 取消 / 恢复）。

Worker 在节点边界检查控制请求：
- pause：请求暂停 —— Worker 在完成当前节点后停止消费，任务状态置为 PAUSED，
  LangGraph Checkpoint（thread_id=task_id）保留执行进度，恢复时断点续跑；
- cancel：请求取消 —— Worker 在节点边界终止执行，任务状态置为 CANCELLED。

进程内注册表 + 任务持久化状态双轨：API 进程与 Worker 同进程时直接生效；
独立部署（Redis 队列 + 多进程）时以任务持久化状态（PAUSED/CANCELLED）为兜底信号。
"""

from __future__ import annotations

import threading

from app.config.logging import get_logger

logger = get_logger(__name__)


class TaskControlService:
    """
    进程内任务控制登记。

    pause/cancel 请求先登记在内存（Worker 每节点轮询），
    同时由调用方将任务状态置为 PAUSED/CANCELLED 持久化。
    """

    def __init__(self) -> None:
        self._pause: set[str] = set()
        self._cancel: set[str] = set()
        self._lock = threading.Lock()

    # ---- 登记 ----

    def request_pause(self, task_id: str) -> None:
        """登记暂停请求。"""
        with self._lock:
            self._pause.add(task_id)
            self._cancel.discard(task_id)

    def request_cancel(self, task_id: str) -> None:
        """登记取消请求。"""
        with self._lock:
            self._cancel.add(task_id)
            self._pause.discard(task_id)

    def clear_pause(self, task_id: str) -> None:
        """清除暂停请求（恢复时调用）。"""
        with self._lock:
            self._pause.discard(task_id)

    def clear(self, task_id: str) -> None:
        """清除该任务的全部控制请求。"""
        with self._lock:
            self._pause.discard(task_id)
            self._cancel.discard(task_id)

    def reset_all(self) -> None:
        """清空全部登记（测试/运维用）。"""
        with self._lock:
            self._pause.clear()
            self._cancel.clear()

    # ---- 查询 ----

    def should_pause(self, task_id: str) -> bool:
        """是否请求了暂停。"""
        with self._lock:
            return task_id in self._pause

    def should_cancel(self, task_id: str) -> bool:
        """是否请求了取消。"""
        with self._lock:
            return task_id in self._cancel


_control: TaskControlService | None = None
_control_lock = threading.Lock()


def get_task_control() -> TaskControlService:
    """获取全局任务控制服务单例。"""
    global _control
    if _control is None:
        with _control_lock:
            if _control is None:
                _control = TaskControlService()
    return _control
