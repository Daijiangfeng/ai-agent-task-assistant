"""
任务相关数据模型。
定义任务状态枚举和子任务结构。
"""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING, Optional

from pydantic import BaseModel, Field


class TaskStatus(str, Enum):
    """任务生命周期状态枚举。"""

    PENDING = "pending"
    PLANNING = "planning"
    EXECUTING = "executing"
    REFLECTING = "reflecting"
    REPLANNING = "replanning"
    COMPLETED = "completed"
    FAILED = "failed"


class SubTask(BaseModel):
    """
    子任务模型。
    由 Planner Agent 生成，由 Executor Agent 执行。
    """

    id: str = Field(description="子任务唯一标识")
    description: str = Field(description="子任务描述")
    status: TaskStatus = Field(default=TaskStatus.PENDING, description="子任务状态")
    result: Optional[str] = Field(default=None, description="执行结果")
    tool_used: Optional[str] = Field(default=None, description="使用的工具名称")
    error: Optional[str] = Field(default=None, description="错误信息")
    dependencies: list[str] = Field(default_factory=list, description="依赖的其他子任务 ID 列表")


class Task(BaseModel):
    """
    顶层任务模型。
    表示一次完整的 Agent 任务执行。
    """

    id: str = Field(description="任务唯一标识")
    goal: str = Field(description="用户目标描述")
    context: Optional[str] = Field(default=None, description="附加上下文信息")
    status: TaskStatus = Field(default=TaskStatus.PENDING, description="任务状态")
    subtasks: list[SubTask] = Field(default_factory=list, description="子任务列表")
    plan: Optional["Plan"] = Field(default=None, description="执行计划（Planner 生成）")
    reflection: Optional["ReflectionResult"] = Field(
        default=None, description="最新一次反思评估结果"
    )
    plan_version: int = Field(default=1, description="计划版本号")
    iteration_count: int = Field(default=0, description="重规划迭代次数")
    final_result: Optional[str] = Field(default=None, description="最终执行结果")
    error: Optional[str] = Field(default=None, description="全局错误信息")
    created_at: str = Field(description="创建时间（ISO 格式）")
    updated_at: str = Field(description="更新时间（ISO 格式）")


# Plan / ReflectionResult 的真实类型在 app/models/__init__.py 中注入并触发 model_rebuild，
# 以打破 task.py 与 plan.py 的循环导入（plan.py 依赖本模块的 SubTask）。
if TYPE_CHECKING:
    from app.models.plan import Plan, ReflectionResult
