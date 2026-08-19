"""
Agent Trace 系统。

按 OpenTelemetry 语义约定（trace.span / attributes）记录每个任务的执行轨迹，
供执行监控、调试、成本核算与安全审计：

- Run: 任务级 trace（task_id、goal、调用者、状态、总用量与成本）；
- NodeSpan: 节点级 span（planner/executor/reflection/replanner，耗时、
  节点内 LLM 用量归因）；
- ToolCallEvent: 工具调用事件（工具名、放行/拒绝、耗时）。

存储为进程内环形缓存（默认保留最近 TRACE_MAX_RUNS 条），并通过 structlog
结构化日志输出（合并进现有日志流水线）；可在此之上扩展导出器（OTLP、
数据库等）而不影响调用方。

线程安全：FastAPI 与 Worker 可能在不同事件循环中读写，使用锁保护。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any

from app.config.logging import get_logger

logger = get_logger(__name__)

TRACE_MAX_RUNS = 500


@dataclass
class ToolCallEvent:
    """一次工具调用事件（放行/拒绝均记录，供审计）。"""

    tool: str
    allowed: bool
    latency_ms: float = 0.0
    reason: str | None = None
    error: str | None = None
    args: dict[str, Any] | None = None
    approval_required: bool | None = None
    approval_result: str | None = None
    at: float = field(default_factory=time.time)


@dataclass
class AgentStepEvent:
    """Agent 级执行步骤事件（Supervisor/SubAgent/Reviewer 全链路追踪）。"""

    agent_name: str
    parent_agent: str = ""
    input: str = ""
    context_snapshot: dict[str, Any] = field(default_factory=dict)
    extracted_requirements: dict[str, Any] = field(default_factory=dict)
    missing_requirements: list[str] = field(default_factory=list)
    tool_name: str | None = None
    tool_arguments: dict[str, Any] | None = None
    approval_required: bool | None = None
    approval_result: str | None = None
    tool_result: str | None = None
    output: str | None = None
    error: str | None = None
    latency_ms: float = 0.0
    at: float = field(default_factory=time.time)


@dataclass
class NodeSpan:
    """节点级执行 span（含节点内 LLM 用量归因）。"""

    name: str
    started_at: float
    duration_ms: float = 0.0
    llm_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0


@dataclass
class TaskTrace:
    """任务级 trace（对应 OTel 的一条 trace）。"""

    task_id: str
    goal: str
    user_id: str
    tenant_id: str
    status: str = "running"
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    error: str | None = None
    nodes: list[NodeSpan] = field(default_factory=list)
    tool_calls: list[ToolCallEvent] = field(default_factory=list)
    agent_steps: list[AgentStepEvent] = field(default_factory=list)
    llm_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    @property
    def duration_ms(self) -> float:
        end = self.finished_at or time.time()
        return round((end - self.started_at) * 1000, 1)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "goal": self.goal,
            "user_id": self.user_id,
            "tenant_id": self.tenant_id,
            "status": self.status,
            "error": self.error,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_ms": self.duration_ms,
            "llm_calls": self.llm_calls,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "cost_usd": round(self.cost_usd, 6),
            "nodes": [
                {
                    "name": n.name,
                    "started_at": n.started_at,
                    "duration_ms": round(n.duration_ms, 1),
                    "llm_calls": n.llm_calls,
                    "prompt_tokens": n.prompt_tokens,
                    "completion_tokens": n.completion_tokens,
                    "cost_usd": round(n.cost_usd, 6),
                }
                for n in self.nodes
            ],
            "tool_calls": [
                {
                    "tool": e.tool,
                    "allowed": e.allowed,
                    "latency_ms": round(e.latency_ms, 1),
                    "reason": e.reason,
                    "error": e.error,
                    "args": e.args,
                    "approval_required": e.approval_required,
                    "approval_result": e.approval_result,
                    "at": e.at,
                }
                for e in self.tool_calls
            ],
            "agent_steps": [
                {
                    "agent_name": s.agent_name,
                    "parent_agent": s.parent_agent,
                    "input": s.input,
                    "context_snapshot": s.context_snapshot,
                    "extracted_requirements": s.extracted_requirements,
                    "missing_requirements": s.missing_requirements,
                    "tool_name": s.tool_name,
                    "tool_arguments": s.tool_arguments,
                    "approval_required": s.approval_required,
                    "approval_result": s.approval_result,
                    "tool_result": s.tool_result,
                    "output": s.output,
                    "error": s.error,
                    "latency_ms": round(s.latency_ms, 1),
                    "at": s.at,
                }
                for s in self.agent_steps
            ],
        }


class TraceRecorder:
    """进程内 Trace 记录器（环形缓存 + 结构化日志）。"""

    def __init__(self, max_runs: int = TRACE_MAX_RUNS):
        self._max_runs = max_runs
        self._runs: dict[str, TaskTrace] = {}
        self._order: list[str] = []
        self._lock = threading.Lock()

    def clear(self) -> None:
        """清空全部记录（测试与运维用）。"""
        with self._lock:
            self._runs.clear()
            self._order.clear()

    # ---- Run 生命周期 ----

    def start_run(
        self,
        task_id: str,
        goal: str,
        user_id: str = "anonymous",
        tenant_id: str = "default",
    ) -> TaskTrace:
        with self._lock:
            trace = TaskTrace(
                task_id=task_id,
                goal=goal,
                user_id=user_id,
                tenant_id=tenant_id,
            )
            self._runs[task_id] = trace
            self._order.append(task_id)
            # 环形裁剪：仅保留最近 max_runs 条
            while len(self._order) > self._max_runs:
                old = self._order.pop(0)
                self._runs.pop(old, None)
        logger.info(
            "Trace: 任务开始",
            task_id=task_id,
            goal=goal,
            user_id=user_id,
            tenant_id=tenant_id,
        )
        return trace

    def finish_run(self, task_id: str, status: str, error: str | None = None) -> None:
        trace = self._runs.get(task_id)
        if trace is None:
            return
        with self._lock:
            trace.status = status
            trace.error = error
            trace.finished_at = time.time()
        logger.info(
            "Trace: 任务结束",
            task_id=task_id,
            status=status,
            error=error,
            duration_ms=trace.duration_ms,
            llm_calls=trace.llm_calls,
            total_tokens=trace.total_tokens,
            cost_usd=round(trace.cost_usd, 6),
        )

    def record_run_usage(
        self,
        task_id: str,
        llm_calls: int,
        prompt_tokens: int,
        completion_tokens: int,
        cost_usd: float,
    ) -> None:
        trace = self._runs.get(task_id)
        if trace is None:
            return
        with self._lock:
            trace.llm_calls = llm_calls
            trace.prompt_tokens = prompt_tokens
            trace.completion_tokens = completion_tokens
            trace.cost_usd = cost_usd

    # ---- Node Span ----

    def add_node_span(
        self,
        task_id: str,
        name: str,
        started_at: float,
        duration_ms: float,
        llm_calls: int = 0,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        cost_usd: float = 0.0,
    ) -> None:
        trace = self._runs.get(task_id)
        if trace is None:
            return
        with self._lock:
            trace.nodes.append(
                NodeSpan(
                    name=name,
                    started_at=started_at,
                    duration_ms=duration_ms,
                    llm_calls=llm_calls,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    cost_usd=cost_usd,
                )
            )
        logger.info(
            "Trace: 节点完成",
            task_id=task_id,
            node=name,
            duration_ms=round(duration_ms, 1),
            llm_calls=llm_calls,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )

    # ---- Tool Call ----

    def record_tool_call(
        self,
        task_id: str,
        tool: str,
        allowed: bool,
        latency_ms: float = 0.0,
        reason: str | None = None,
        error: str | None = None,
        args: dict[str, Any] | None = None,
        approval_required: bool | None = None,
        approval_result: str | None = None,
    ) -> None:
        trace = self._runs.get(task_id)
        if trace is None:
            return
        with self._lock:
            trace.tool_calls.append(
                ToolCallEvent(
                    tool=tool,
                    allowed=allowed,
                    latency_ms=latency_ms,
                    reason=reason,
                    error=error,
                    args=args,
                    approval_required=approval_required,
                    approval_result=approval_result,
                )
            )

    # ---- Agent Step ----

    def record_agent_step(self, task_id: str, event: AgentStepEvent) -> None:
        """记录一次 Agent 级执行步骤（Supervisor/SubAgent/Reviewer 全链路）。"""
        trace = self._runs.get(task_id)
        if trace is None:
            return
        with self._lock:
            trace.agent_steps.append(event)
        logger.info(
            "Trace: Agent 步骤",
            task_id=task_id,
            agent=event.agent_name,
            parent=event.parent_agent or None,
            latency_ms=round(event.latency_ms, 1),
            error=event.error,
        )

    # ---- 查询 ----

    def get_trace(self, task_id: str) -> TaskTrace | None:
        with self._lock:
            trace = self._runs.get(task_id)
            return trace

    def list_traces(self, limit: int = 20, offset: int = 0) -> list[TaskTrace]:
        with self._lock:
            ids = self._order[-limit - offset:] if offset else self._order[-limit:]
            return [self._runs[i] for i in ids]


_recorder: TraceRecorder | None = None
_recorder_lock = threading.Lock()


def get_trace_recorder() -> TraceRecorder:
    """获取全局 Trace 记录器单例。"""
    global _recorder
    if _recorder is None:
        with _recorder_lock:
            if _recorder is None:
                _recorder = TraceRecorder()
    return _recorder
