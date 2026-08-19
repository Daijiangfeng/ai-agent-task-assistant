"""
Agent Trace API 路由。
提供任务执行轨迹查询：节点时间线、LLM 用量、工具调用、成本，
供执行监控、调试与成本核算（数据源：进程内 TraceRecorder）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.auth import get_current_user
from app.tools.security import ToolContext
from app.tracing.recorder import get_trace_recorder

router = APIRouter(prefix="/traces", tags=["traces"])


@router.get("")
async def list_traces(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    user: ToolContext = Depends(get_current_user),
):
    """列出最近的任务执行轨迹（按结束时间倒序）。"""
    recorder = get_trace_recorder()
    traces = recorder.list_traces(limit=limit, offset=offset)
    return {
        "total": len(traces),
        "traces": [t.to_dict() for t in reversed(traces)],
    }


@router.get("/{task_id}")
async def get_trace(task_id: str):
    """查询单个任务的执行轨迹详情（节点时间线 / 用量 / 工具调用 / 成本）。"""
    recorder = get_trace_recorder()
    trace = recorder.get_trace(task_id)
    if trace is None:
        raise HTTPException(status_code=404, detail=f"任务 {task_id} 暂无执行轨迹")
    return trace.to_dict()
