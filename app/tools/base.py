"""
工具抽象基类和数据结构。
定义所有工具必须实现的统一接口。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel

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
            query: str = "", parameters: dict[str, Any] | None = None
        ) -> str:
            # 同时转发自由文本 query 与结构化 parameters，
            # 使 sql_query / file_processing 等需要结构化入参的工具可正确取参。
            result = await self.execute(
                ToolInput(query=query, parameters=parameters or {})
            )
            if result.success:
                return str(result.data)
            return f"工具执行失败: {result.error}"

        return StructuredTool.from_function(
            coroutine=_arun,
            name=self.name,
            description=self.description,
        )
