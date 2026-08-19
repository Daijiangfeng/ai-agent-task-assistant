"""
API 请求/响应 Schema 定义。
用于 FastAPI 路由层的输入验证和输出序列化。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.models.plan import Plan, ReflectionResult
from app.models.task import ApprovalRequest, SubTask, TaskStatus


class CreateTaskRequest(BaseModel):
    """创建任务请求体。"""

    goal: str = Field(min_length=1, max_length=10000, description="用户目标描述")
    context: str | None = Field(
        default=None, max_length=50000, description="可选的上下文信息"
    )


class TaskResponse(BaseModel):
    """任务响应体（创建/执行后返回）。"""

    task_id: str = Field(description="任务 ID")
    status: TaskStatus = Field(description="任务状态")
    plan: Plan | None = Field(default=None, description="执行计划")
    created_at: str = Field(description="创建时间")


class TaskStatusResponse(BaseModel):
    """任务状态查询响应体。"""

    task_id: str = Field(description="任务 ID")
    status: TaskStatus = Field(description="任务状态")
    current_step: str | None = Field(default=None, description="当前执行步骤描述")
    progress: float = Field(ge=0.0, le=100.0, description="进度百分比 (0-100)")
    plan: Plan | None = Field(default=None, description="执行计划")
    subtasks: list[SubTask] = Field(default_factory=list, description="子任务列表（含执行状态）")
    reflection: ReflectionResult | None = Field(
        default=None, description="反思评估结果"
    )
    iteration_count: int = Field(default=0, description="重规划迭代次数")
    plan_version: int = Field(default=1, description="计划版本号")
    execution_mode: str | None = Field(
        default=None, description="执行模式: single | multi_agent"
    )
    agent_results: list[dict] = Field(
        default_factory=list, description="多 Agent 模式下各子 Agent 结果"
    )
    pending_approval: ApprovalRequest | None = Field(
        default=None, description="待处理的人工审批请求"
    )
    approval_history: list[ApprovalRequest] = Field(
        default_factory=list, description="历史审批记录"
    )
    error: str | None = Field(default=None, description="全局错误信息")
    final_result: str | None = Field(default=None, description="最终执行结果")


class TaskListResponse(BaseModel):
    """任务列表响应体。"""

    total: int = Field(description="总任务数")
    tasks: list[TaskResponse] = Field(description="任务列表")


class HealthResponse(BaseModel):
    """健康检查响应体。"""

    status: str = "ok"
    version: str = Field(description="应用版本号")


class LivenessResponse(BaseModel):
    """存活探针响应体（进程存活即 alive，不依赖外部基础设施）。"""

    status: str = "alive"
    version: str = Field(description="应用版本号")


class ComponentStatusSchema(BaseModel):
    """单组件健康状态。"""

    name: str = Field(description="组件名（database/queue/vector_db/llm/storage/checkpoint/redis）")
    status: str = Field(description="up | down | skipped")
    detail: str = Field(default="", description="状态说明")
    latency_ms: float = Field(default=0.0, description="探测耗时（毫秒）")
    core: bool = Field(default=True, description="是否核心组件（down 时阻断就绪）")


class ReadinessResponse(BaseModel):
    """就绪探针响应体（核心基础设施可用才 ready）。"""

    status: str = Field(description="ready | not_ready")
    ready: bool = Field(description="是否就绪")
    environment: str = Field(description="运行环境（development/production）")
    version: str = Field(description="应用版本号")
    components: list[ComponentStatusSchema] = Field(default_factory=list)


class ToolInfo(BaseModel):
    """单个工具信息。"""

    name: str = Field(description="工具名称")
    description: str = Field(description="工具描述")


class ToolListResponse(BaseModel):
    """工具列表响应体。"""

    total: int = Field(description="工具总数")
    tools: list[ToolInfo] = Field(description="已注册工具列表")


class StatsResponse(BaseModel):
    """系统概览统计响应体（供仪表盘）。"""

    version: str = Field(description="应用版本号")
    task_total: int = Field(description="任务总数")
    tasks_by_status: dict[str, int] = Field(description="各状态任务计数")
    tool_count: int = Field(description="已注册工具数")


# ---------------------------------------------------------------------------
# Human-in-the-loop 审批 / 任务控制 / 重试
# ---------------------------------------------------------------------------


class ApprovalDecideRequest(BaseModel):
    """审批决策请求体（批准 / 拒绝共用）。"""

    note: str | None = Field(default=None, max_length=2000, description="决策备注")
    modified_args: dict | None = Field(
        default=None, description="批准时修改后的工具参数（可选）"
    )


class RetryTaskRequest(BaseModel):
    """任务重试请求体。"""

    from_index: int | None = Field(
        default=None,
        ge=0,
        description="从指定子任务索引重新执行；不传则从头重新规划执行",
    )


# ---------------------------------------------------------------------------
# 任务模板 / Agent Skill
# ---------------------------------------------------------------------------


class AgentTemplateCreate(BaseModel):
    """创建任务模板请求体。"""

    name: str = Field(min_length=1, max_length=100, description="模板名称")
    description: str = Field(default="", max_length=2000, description="模板描述")
    category: str = Field(
        default="general",
        description="模板类别（market_research/document_analysis/code_review/general 等）",
    )
    goal_template: str = Field(
        min_length=1, max_length=10000, description="目标模板，支持 {var} 变量占位"
    )
    context_template: str | None = Field(
        default=None, max_length=50000, description="上下文模板，支持 {var} 变量占位"
    )
    tags: list[str] = Field(default_factory=list, description="标签")


class AgentTemplateUpdate(BaseModel):
    """更新任务模板请求体（全部可选，仅更新传入字段）。"""

    name: str | None = Field(default=None, max_length=100)
    description: str | None = Field(default=None, max_length=2000)
    category: str | None = Field(default=None)
    goal_template: str | None = Field(default=None, max_length=10000)
    context_template: str | None = Field(default=None, max_length=50000)
    tags: list[str] | None = Field(default=None)


class AgentTemplateResponse(BaseModel):
    """任务模板响应体。"""

    id: str = Field(description="模板 ID")
    name: str = Field(description="模板名称")
    description: str = Field(description="模板描述")
    category: str = Field(description="模板类别")
    goal_template: str = Field(description="目标模板")
    context_template: str | None = Field(default=None, description="上下文模板")
    tags: list[str] = Field(default_factory=list, description="标签")
    variables: list[str] = Field(
        default_factory=list, description="模板中的变量占位符列表"
    )
    is_builtin: bool = Field(description="是否为内置模板")
    created_at: str = Field(description="创建时间")
    updated_at: str = Field(description="更新时间")


class TemplateListResponse(BaseModel):
    """模板列表响应体。"""

    total: int = Field(description="模板总数")
    templates: list[AgentTemplateResponse] = Field(description="模板列表")


class TemplateRunRequest(BaseModel):
    """基于模板创建任务请求体。"""

    inputs: dict[str, str] = Field(
        default_factory=dict, description="模板变量值，用于渲染 goal/context"
    )
    auto_execute: bool = Field(
        default=False, description="创建后是否立即入队执行"
    )
