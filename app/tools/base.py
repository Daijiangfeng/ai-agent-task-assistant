"""
工具抽象基类和数据结构。
定义所有工具必须实现的统一接口。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel

from app.tools.schema import (
    TOOL_CATEGORY_BY_LEGACY,
    ExecutionMode,
    ToolCategory,
    ToolSchema,
)
from app.tools.security import (
    CATEGORY_SYSTEM,
    ToolContext,
    is_role_allowed,
)


class ToolInput(BaseModel):
    """工具输入基类，子类按需扩展。"""
    query: str = ""
    parameters: dict[str, Any] = {}


class ToolOutput(BaseModel):
    """工具输出基类。"""
    success: bool
    data: Any = None
    error: str | None = None


class BaseTool(ABC):
    """
    工具抽象基类。
    所有工具（Web Search、RAG、SQL Query、File Processing 等）必须继承此类。

    category 声明工具的权限类别（system/rag/sql/file/network），
    由权限矩阵（app/tools/security.py）按调用者角色决定是否放行。
    """

    category: str = CATEGORY_SYSTEM

    # ---- 统一 Tool Runtime 元数据（五类能力/Schema/权限/执行模式）----
    # 新工具保留 category 为旧类别字符串（兼容既有权限矩阵与 multi_agent 作用域），
    # 同时用 runtime_category 声明其五类能力归属（Observe/Reason/Act/Remember/Interact）。
    runtime_category: ToolCategory | None = None
    execution_mode: ExecutionMode = ExecutionMode.SYNC
    timeout: float | None = None  # 执行超时（秒）；None 用 ToolExecutor 默认
    permissions: frozenset[str] = frozenset()  # 所需权限字符串，如 {"observe:web"}
    metadata: dict[str, Any] = {}  # 附加元数据（idempotent/risk/version 等）
    available: bool = True  # 基础设施是否就绪（Mock 实现为 False）
    input_schema: dict[str, Any] | None = None  # JSON Schema 风格入参
    output_schema: dict[str, Any] | None = None  # JSON Schema 风格出参
    required_params: list[str] = []  # 必填参数名（调用前参数完整性检查用）

    def to_schema(self) -> ToolSchema:
        """生成统一 ToolSchema（用于 LLM Function Calling 与权限/校验）。"""
        runtime_cat = self.runtime_category or TOOL_CATEGORY_BY_LEGACY.get(
            self.category, ToolCategory.REASON
        )
        return ToolSchema(
            name=self.name,
            description=self.description,
            category=runtime_cat,
            input_schema=self.input_schema,
            output_schema=self.output_schema,
            required_params=list(self.required_params),
            permissions=frozenset(self.permissions),
            execution_mode=self.execution_mode,
            timeout=self.timeout,
            available=self.available,
            metadata=dict(self.metadata),
        )

    def to_function_schema(self) -> dict[str, Any]:
        """转换为 LLM Function Calling 的 tool schema（dict 形式）。"""
        return self.to_schema().to_function_schema()

    def _authorize(self, context: ToolContext | None) -> str | None:
        """
        按调用者角色校验当前工具类别是否允许（权限矩阵）。

        Args:
            context: 调用者身份上下文。为 None 时视为内部可信调用，放行；
                外部调用（Agent Executor / API 直调）必须显式携带身份。

        Returns:
            允许返回 None，否则返回错误消息。
        """
        if context is None:
            return None
        if not is_role_allowed(context.role, self.category):
            return (
                f"当前角色 '{context.role}' 无权调用工具 {self.name} "
                f"（类别: {self.category}）"
            )
        return None

    @property
    @abstractmethod
    def name(self) -> str:
        """工具名称。"""
        ...

    @property
    @abstractmethod
    def description(self) -> str:
        """工具描述，用于 Planner 选择工具。"""
        ...

    @abstractmethod
    async def execute(self, input: ToolInput) -> ToolOutput:
        """
        执行工具逻辑。

        Args:
            input: 工具输入参数。

        Returns:
            ToolOutput 执行结果。
        """
        ...

    def to_langchain_tool(self):
        """
        转换为 LangChain Tool 对象，供 Agent 使用。

        Returns:
            LangChain Tool 实例。
        """
        from langchain_core.tools import StructuredTool

        async def _arun(
            query: str = "",
            parameters: dict[str, Any] | None = None,
            **kwargs: Any,
        ) -> str:
            # 编译入参：query 为自由文本；parameters 为结构化参数；
            # 其余 keyword 参数（来自工具 input_schema 的具名入参）合并进 parameters，
            # 使 code.execute / http.get / database.write 等 schema 驱动工具可正确取参。
            params = dict(parameters or {})
            params.update(kwargs)
            result = await self.execute(
                ToolInput(query=query, parameters=params)
            )
            if result.success:
                return str(result.data)
            return f"工具执行失败: {result.error}"

        return StructuredTool.from_function(
            coroutine=_arun,
            name=self.name,
            description=self.description,
        )
