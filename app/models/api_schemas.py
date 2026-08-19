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


class IngestDocumentRequest(BaseModel):
    """文档入库请求体。"""

    file_path: str = Field(min_length=1, description="待索引的文件路径")


class IngestDocumentResponse(BaseModel):
    """文档入库响应体。"""

    source: str = Field(description="文档来源路径")
    chunks_indexed: int = Field(description="已索引的分块数量")


class KnowledgeSearchRequest(BaseModel):
    """知识库检索请求体。"""

    query: str = Field(min_length=1, max_length=5000, description="检索查询文本")
    top_k: int | None = Field(default=None, ge=1, le=50, description="返回数量")


class KnowledgeSearchResult(BaseModel):
    """单条知识检索结果。"""

    content: str = Field(description="文档片段内容")
    metadata: dict = Field(default_factory=dict, description="元数据")
    score: float | None = Field(default=None, description="相关度分数")
    rerank_score: float | None = Field(
        default=None, description="Rerank 精排相关性分数（启用 rerank 时提供）"
    )


class KnowledgeSearchResponse(BaseModel):
    """知识库检索响应体。"""

    query: str = Field(description="检索查询文本")
    results: list[KnowledgeSearchResult] = Field(description="检索结果列表")


class DocumentInfo(BaseModel):
    """已索引文档概览（按来源聚合）。"""

    source: str = Field(description="文档来源路径")
    type: str | None = Field(default=None, description="文档类型")
    chunk_count: int = Field(description="该文档的分块数")


class DocumentListResponse(BaseModel):
    """已索引文档列表响应体。"""

    total: int = Field(description="文档总数（按来源去重）")
    documents: list[DocumentInfo] = Field(description="文档列表")


class DeleteDocumentResponse(BaseModel):
    """删除文档响应体。"""

    source: str = Field(description="被删除的文档来源")
    chunks_deleted: int = Field(description="被删除的分块数")


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
    knowledge_document_count: int = Field(description="知识库文档数（按来源去重）")
    knowledge_chunk_count: int = Field(description="知识库分块总数")


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
