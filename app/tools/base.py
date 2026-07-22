"""
工具抽象基类和数据结构。
定义所有工具必须实现的统一接口。
"""

from abc import ABC, abstractmethod
from typing import Any, Optional

from pydantic import BaseModel


class ToolInput(BaseModel):
    """工具输入基类，子类按需扩展。"""
    query: str = ""
    parameters: dict[str, Any] = {}


class ToolOutput(BaseModel):
    """工具输出基类。"""
    success: bool
    data: Any = None
    error: Optional[str] = None


class BaseTool(ABC):
    """
    工具抽象基类。
    所有工具（Web Search、RAG、SQL Query、File Processing 等）必须继承此类。
    """

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

        async def _arun(query: str) -> str:
            result = await self.execute(ToolInput(query=query))
            if result.success:
                return str(result.data)
            return f"工具执行失败: {result.error}"

        return StructuredTool.from_function(
            coroutine=_arun,
            name=self.name,
            description=self.description,
        )
