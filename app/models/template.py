"""
任务模板 / Agent Skill 数据模型。

模板把常见工作流（市场调研、文档分析、代码审查等）固化为可复用配方：
goal_template / context_template 支持 {var} 变量占位，创建任务时注入实际值渲染。
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

_VARIABLE_PATTERN = re.compile(r"\{(\w+)\}")


class AgentTemplate(BaseModel):
    """任务模板（Agent Skill / Workflow Template）。"""

    id: str = Field(description="模板唯一标识")
    name: str = Field(description="模板名称")
    description: str = Field(default="", description="模板描述")
    category: str = Field(
        default="general",
        description="模板类别（market_research/document_analysis/code_review/general 等）",
    )
    goal_template: str = Field(description="目标模板，支持 {var} 变量占位")
    context_template: str | None = Field(
        default=None, description="上下文模板，支持 {var} 变量占位"
    )
    tags: list[str] = Field(default_factory=list, description="标签")
    is_builtin: bool = Field(default=False, description="是否为内置模板")
    created_at: str = Field(description="创建时间（ISO 格式）")
    updated_at: str = Field(description="更新时间（ISO 格式）")

    def variables(self) -> list[str]:
        """提取模板中的变量占位符列表（去重保序）。"""
        found: list[str] = []
        for text in (self.goal_template, self.context_template or ""):
            for match in _VARIABLE_PATTERN.findall(text):
                if match not in found:
                    found.append(match)
        return found

    def render(self, inputs: dict[str, str]) -> tuple[str, str | None]:
        """
        用输入值渲染模板。

        未提供的变量替换为空字符串；保留位置以便用户后续补充。
        """
        def _sub(text: str) -> str:
            def _replace(match: re.Match) -> str:
                return inputs.get(match.group(1), "")
            return _VARIABLE_PATTERN.sub(_replace, text)

        goal = _sub(self.goal_template).strip()
        context = _sub(self.context_template).strip() if self.context_template else None
        return goal, (context or None)
