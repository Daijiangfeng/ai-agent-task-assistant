"""
API 请求/响应 Schema 定义。
用于 FastAPI 路由层的输入验证和输出序列化。
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from app.models.plan import Plan
from app.models.task import TaskStatus


class CreateTaskRequest(BaseModel):
    """创建任务请求体。"""

    goal: str = Field(min_length=1, description="用户目标描述")
    context: Optional[str] = Field(default=None, description="可选的上下文信息")


class TaskResponse(BaseModel):
    """任务响应体（创建/执行后返回）。"""

    task_id: str = Field(description="任务 ID")
    status: TaskStatus = Field(description="任务状态")
    plan: Optional[Plan] = Field(default=None, description="执行计划")
    created_at: str = Field(description="创建时间")


class TaskStatusResponse(BaseModel):
    """任务状态查询响应体。"""

    task_id: str = Field(description="任务 ID")
    status: TaskStatus = Field(description="任务状态")
    current_step: Optional[str] = Field(default=None, description="当前执行步骤描述")
    progress: float = Field(ge=0.0, le=100.0, description="进度百分比 (0-100)")
    plan: Optional[Plan] = Field(default=None, description="执行计划")
    final_result: Optional[str] = Field(default=None, description="最终执行结果")


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

    query: str = Field(min_length=1, description="检索查询文本")
    top_k: Optional[int] = Field(default=None, ge=1, le=50, description="返回数量")


class KnowledgeSearchResult(BaseModel):
    """单条知识检索结果。"""

    content: str = Field(description="文档片段内容")
    metadata: dict = Field(default_factory=dict, description="元数据")
    score: Optional[float] = Field(default=None, description="相关度分数")


class KnowledgeSearchResponse(BaseModel):
    """知识库检索响应体。"""

    query: str = Field(description="检索查询文本")
    results: list[KnowledgeSearchResult] = Field(description="检索结果列表")
