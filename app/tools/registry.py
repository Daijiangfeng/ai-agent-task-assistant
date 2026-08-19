"""
工具注册表。
单例模式管理所有可用工具。
"""

from app.tools.base import BaseTool


class ToolRegistry:
    """
    工具注册表（单例）。

    在应用启动时注册所有工具实例，
    Agent 通过此注册表获取可用工具列表。
    """

    _tools: dict[str, BaseTool] = {}

    @classmethod
    def register(cls, tool: BaseTool) -> None:
        """
        注册工具实例。

        Args:
            tool: BaseTool 子类实例。

        重复注册同名工具：覆盖旧实例并记录告警（防重复注册但不阻断）。
        """
        if tool.name in cls._tools and cls._tools[tool.name] is not tool:
            from app.config.logging import get_logger

            get_logger(__name__).warning(
                "ToolRegistry: 覆盖同名工具", name=tool.name
            )
        cls._tools[tool.name] = tool

    @classmethod
    def unregister(cls, name: str) -> None:
        """注销工具（不存在则静默忽略）。"""
        cls._tools.pop(name, None)

    @classmethod
    def get(cls, name: str) -> BaseTool | None:
        """
        按名称获取工具实例。

        Args:
            name: 工具名称。

        Returns:
            BaseTool 实例，不存在则返回 None。
        """
        return cls._tools.get(name)

    @classmethod
    def get_all(cls) -> dict[str, BaseTool]:
        """获取所有已注册的工具。"""
        return dict(cls._tools)

    @classmethod
    def get_tool_descriptions(cls) -> str:
        """
        获取所有工具的描述文本，供 Planner Prompt 使用。

        Returns:
            格式化的工具描述字符串。
        """
        if not cls._tools:
            return "当前无可用工具。"

        lines = []
        for tool in cls._tools.values():
            lines.append(f"- {tool.name}: {tool.description}")
        return "\n".join(lines)

    @classmethod
    def get_all_langchain_tools(cls) -> list:
        """
        获取所有工具的 LangChain Tool 对象列表。
        用于传递给 Agent。
        """
        return [tool.to_langchain_tool() for tool in cls._tools.values()]

    @classmethod
    def get_tools_by_categories(cls, categories: set[str]) -> list[BaseTool]:
        """
        按工具类别过滤已注册工具（多 Agent 角色工具范围控制用）。

        Args:
            categories: 允许的工具类别集合（如 {network, rag}）。

        Returns:
            类别匹配的工具实例列表。
        """
        return [t for t in cls._tools.values() if t.category in categories]

    @classmethod
    def get_langchain_tools_by_categories(cls, categories: set[str]) -> list:
        """获取指定类别工具的 LangChain Tool 对象列表。"""
        return [
            t.to_langchain_tool()
            for t in cls.get_tools_by_categories(categories)
        ]

    @classmethod
    def clear(cls) -> None:
        """清空注册表（用于测试）。"""
        cls._tools.clear()
