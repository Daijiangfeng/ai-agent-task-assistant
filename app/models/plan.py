"""
计划与反思相关数据模型。
定义 Plan（执行计划）和 ReflectionResult（反思评估结果）。
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from app.models.task import SubTask


class Plan(BaseModel):
    """
    执行计划模型。
    由 Planner Agent 生成，包含用户目标拆解后的子任务序列。
    """

    goal: str = Field(description="用户原始目标")
    subtasks: list[SubTask] = Field(description="子任务列表")
    version: int = Field(default=1, description="计划版本号，重规划时递增")
    reasoning: Optional[str] = Field(
        default=None, description="规划推理过程说明"
    )


class ReflectionResult(BaseModel):
    """
    反思评估结果模型。
    由 Reflection Agent 生成，用于判断是否需要重新规划。
    """

    is_satisfactory: bool = Field(description="整体结果是否满意")
    accuracy_score: float = Field(
        ge=0.0, le=1.0, description="准确性评分 (0-1)"
    )
    completeness_score: float = Field(
        ge=0.0, le=1.0, description="完整性评分 (0-1)"
    )
    relevance_score: float = Field(
        ge=0.0, le=1.0, description="相关性评分 (0-1)"
    )
    issues: list[str] = Field(
        default_factory=list, description="发现的问题列表"
    )
    suggestion: Optional[str] = Field(
        default=None, description="改进建议（触发重新规划时使用）"
    )
