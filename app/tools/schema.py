"""
统一 Tool 元数据模型。

定义五类 Agent 能力（Observe/Reason/Act/Remember/Interact）、执行模式
（SYNC/ASYNC/STREAM）以及机器可读的 ToolSchema。

ToolSchema 同时服务于两个目的：
1. LLM Function Calling：通过 to_function_schema() 转换为 Azure/GLM 等
   ChatModel.bind_tools() 可消费的 JSON Schema；
2. 权限/可用性判断：permissions 声明该工具所需的权限字符串，
   available 声明当前基础设施是否具备（Mock 实现标记 available=False）。

本模块不依赖具体 LLM SDK，保持与 Agent Core 解耦。
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ToolCategory(str, Enum):
    """五类 Agent 能力（权限与归类的顶级维度）。"""

    OBSERVE = "observe"  # 获取信息（只读）
    REASON = "reason"  # 处理信息（计算/转换）
    ACT = "act"  # 对外执行操作（副作用）
    REMEMBER = "remember"  # 保存状态（记忆）
    INTERACT = "interact"  # 与用户/环境交互


class ExecutionMode(str, Enum):
    """工具执行模式。"""

    SYNC = "sync"  # 同步，返回最终结果
    ASYNC = "async"  # 提交异步执行，返回执行句柄
    STREAM = "stream"  # 流式产出（当前保留抽象，供后续对象存储/SSE 扩展）


# 五类能力的中文标签（展示与日志用）
CATEGORY_LABELS: dict[ToolCategory, str] = {
    ToolCategory.OBSERVE: "获取信息",
    ToolCategory.REASON: "处理信息",
    ToolCategory.ACT: "对外执行",
    ToolCategory.REMEMBER: "保存状态",
    ToolCategory.INTERACT: "用户交互",
}

# 旧权限矩阵（app/tools/security.py）与新五类的对照，供日志/迁移诊断
LEGACY_CATEGORY_BY_NEW: dict[ToolCategory, str] = {
    ToolCategory.OBSERVE: "network",  # 占位，具体工具会显式声明 legacy_category
    ToolCategory.REASON: "system",
    ToolCategory.ACT: "network",
    ToolCategory.REMEMBER: "system",
    ToolCategory.INTERACT: "system",
}

# 旧类别字符串 -> 五类（用于旧工具 to_schema() 归类展示）
TOOL_CATEGORY_BY_LEGACY: dict[str, ToolCategory] = {
    "system": ToolCategory.REASON,
    "rag": ToolCategory.OBSERVE,
    "sql": ToolCategory.OBSERVE,
    "file": ToolCategory.OBSERVE,
    "network": ToolCategory.OBSERVE,
}


@dataclass(frozen=True)
class ToolSchema:
    """
    工具的统一机器可读 Schema。

    所有字段有默认值，具体 Tool 只需覆写关心的字段，保持与既有
    BaseTool（name/description/category）的兼容。
    """

    name: str
    description: str
    category: ToolCategory = ToolCategory.REASON
    # JSON Schema 风格的入参/出参描述（用于 LLM Function Calling 与输入校验）
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None
    # 必填参数名列表（需求槽位名或入参名，用于调用前的参数完整性检查）
    required_params: list[str] = field(default_factory=list)
    # 所需权限字符串，如 {"observe:web", "act:database"}；空集表示无特殊权限
    permissions: frozenset[str] = field(default_factory=frozenset)
    execution_mode: ExecutionMode = ExecutionMode.SYNC
    # 超时（秒），None 表示用 ToolExecutor 的默认超时
    timeout: float | None = None
    # 该工具当前是否可执行（基础设施齐备）；Mock 实现应为 False
    available: bool = True
    # 附加元数据（幂等性、风险等级、版本等）
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_function_schema(self) -> dict[str, Any]:
        """
        转换为 LLM Function Calling 的 tool schema。

        兼容 langchain bind_tools() 传入的 dict 格式：
        {"type": "function", "function": {"name", "description", "parameters"}}
        """
        params = self.input_schema or {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "查询内容"}},
            "required": ["query"],
        }
        function = {
            "name": self.name,
            "description": self.description or self.name,
            "parameters": copy.deepcopy(params),
        }
        return {"type": "function", "function": function}

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "category": self.category.value,
            "input_schema": copy.deepcopy(self.input_schema),
            "output_schema": copy.deepcopy(self.output_schema),
            "required_params": list(self.required_params),
            "permissions": sorted(self.permissions),
            "execution_mode": self.execution_mode.value,
            "timeout": self.timeout,
            "available": self.available,
            "metadata": copy.deepcopy(self.metadata),
        }

    @property
    def is_idempotent(self) -> bool:
        """是否幂等（决定自动重试策略，Act 类默认否）。"""
        if self.metadata.get("idempotent") is not None:
            return bool(self.metadata["idempotent"])
        return self.category is not ToolCategory.ACT
