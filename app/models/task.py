"""
任务相关数据模型。
定义任务状态枚举和子任务结构。
"""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field


class TaskStatus(str, Enum):
    """任务生命周期状态枚举。"""

    PENDING = "pending"
    PLANNING = "planning"
    EXECUTING = "executing"
    REFLECTING = "reflecting"
    REPLANNING = "replanning"
    AWAITING_APPROVAL = "awaiting_approval"
    PAUSED = "paused"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    FAILED = "failed"


# 可再次执行（重新入队）的状态
RESTARTABLE_STATUSES = frozenset(
    {
        TaskStatus.PENDING,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
        TaskStatus.PAUSED,
        TaskStatus.COMPLETED,
    }
)


class ApprovalStatus(str, Enum):
    """工具审批请求状态。"""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class ApprovalRequest(BaseModel):
    """
    Human-in-the-loop 审批请求。

    Agent 准备执行高风险动作（如删除知识库文档）时暂停执行，
    等待用户批准 / 拒绝 / 修改参数后再继续。
    """

    id: str = Field(description="审批请求唯一标识")
    task_id: str = Field(description="所属任务 ID")
    tool_name: str = Field(description="请求调用的工具名称")
    args: dict = Field(default_factory=dict, description="工具调用参数（详情供用户审阅）")
    reason: str = Field(default="", description="请求审批的原因说明")
    status: ApprovalStatus = Field(
        default=ApprovalStatus.PENDING, description="审批状态"
    )
    created_at: str = Field(description="请求创建时间（ISO 格式）")
    decided_at: str | None = Field(default=None, description="决策时间（ISO 格式）")
    decision_note: str | None = Field(
        default=None, description="用户决策备注（拒绝原因 / 修改说明）"
    )
    modified_args: dict | None = Field(
        default=None, description="用户修改后的工具参数（批准时可选）"
    )


class SubTask(BaseModel):
    """
    子任务模型。
    由 Planner Agent 生成，由 Executor Agent 执行。
    """

    id: str = Field(description="子任务唯一标识")
    description: str = Field(description="子任务描述")
    status: TaskStatus = Field(default=TaskStatus.PENDING, description="子任务状态")
    result: str | None = Field(default=None, description="执行结果")
    tool_used: str | None = Field(default=None, description="使用的工具名称")
    error: str | None = Field(default=None, description="错误信息")
    dependencies: list[str] = Field(default_factory=list, description="依赖的其他子任务 ID 列表")


class Task(BaseModel):
    """
    顶层任务模型。
    表示一次完整的 Agent 任务执行。
    """

    id: str = Field(description="任务唯一标识")
    goal: str = Field(description="用户目标描述")
    context: str | None = Field(default=None, description="附加上下文信息")
    owner_id: str = Field(
        default="anonymous", description="任务所有者（创建者）用户 ID"
    )
    tenant_id: str = Field(
        default="default", description="任务所属租户 ID（多租户数据隔离维度）"
    )
    status: TaskStatus = Field(default=TaskStatus.PENDING, description="任务状态")
    subtasks: list[SubTask] = Field(default_factory=list, description="子任务列表")
    plan: Plan | None = Field(default=None, description="执行计划（Planner 生成）")
    reflection: ReflectionResult | None = Field(
        default=None, description="最新一次反思评估结果"
    )
    plan_version: int = Field(default=1, description="计划版本号")
    iteration_count: int = Field(default=0, description="重规划迭代次数")
    execution_mode: str | None = Field(
        default=None, description="执行模式: single（单 Agent）| multi_agent（多 Agent 协作）"
    )
    agent_results: list[dict] = Field(
        default_factory=list, description="多 Agent 协作模式下各子 Agent 的执行结果"
    )
    pending_approval: ApprovalRequest | None = Field(
        default=None, description="待处理的人工审批请求（Human-in-the-loop）"
    )
    approval_history: list[ApprovalRequest] = Field(
        default_factory=list, description="历史审批记录（含已决策的）"
    )
    final_result: str | None = Field(default=None, description="最终执行结果")
    error: str | None = Field(default=None, description="全局错误信息")
    created_at: str = Field(description="创建时间（ISO 格式）")
    updated_at: str = Field(description="更新时间（ISO 格式）")


# Plan / ReflectionResult 的真实类型在 app/models/__init__.py 中注入并触发 model_rebuild，
# 以打破 task.py 与 plan.py 的循环导入（plan.py 依赖本模块的 SubTask）。
if TYPE_CHECKING:
    from app.models.plan import Plan, ReflectionResult
