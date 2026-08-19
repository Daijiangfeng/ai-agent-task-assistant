"""
Prompt 模板统一管理器。
集中管理所有 Agent 的 Prompt 模板，支持注册和获取。
"""

from langchain_core.prompts import ChatPromptTemplate

from app.prompts.executor import EXECUTOR_PROMPT
from app.prompts.multi_agent import (
    REVIEWER_PROMPT,
    SUB_AGENT_PROMPT,
    SUPERVISOR_PROMPT,
)
from app.prompts.planner import PLANNER_PROMPT, REPLANNER_PROMPT
from app.prompts.reflection import REFLECTION_PROMPT


class PromptManager:
    """
    Prompt 模板统一管理器。

    在应用启动时注册所有 Prompt 模板，
    各 Agent 通过名称获取对应的 Prompt 模板。
    """

    _templates: dict[str, ChatPromptTemplate] = {}

    @classmethod
    def register(cls, name: str, template: ChatPromptTemplate) -> None:
        """
        注册 Prompt 模板。

        Args:
            name: 模板名称。
            template: LangChain ChatPromptTemplate 实例。
        """
        cls._templates[name] = template

    @classmethod
    def get(cls, name: str) -> ChatPromptTemplate:
        """
        获取 Prompt 模板。

        Args:
            name: 模板名称。

        Returns:
            ChatPromptTemplate 实例。

        Raises:
            KeyError: 模板不存在。
        """
        if name not in cls._templates:
            raise KeyError(f"Prompt 模板 '{name}' 未注册。可用: {list(cls._templates.keys())}")
        return cls._templates[name]

    @classmethod
    def get_planner_prompt(cls) -> ChatPromptTemplate:
        """获取 Planner Prompt 模板。"""
        return cls.get("planner")

    @classmethod
    def get_replanner_prompt(cls) -> ChatPromptTemplate:
        """获取 Replanner Prompt 模板。"""
        return cls.get("replanner")

    @classmethod
    def get_executor_prompt(cls) -> ChatPromptTemplate:
        """获取 Executor Prompt 模板。"""
        return cls.get("executor")

    @classmethod
    def get_reflection_prompt(cls) -> ChatPromptTemplate:
        """获取 Reflection Prompt 模板。"""
        return cls.get("reflection")

    @classmethod
    def get_supervisor_prompt(cls) -> ChatPromptTemplate:
        """获取 Supervisor Prompt 模板。"""
        return cls.get("supervisor")

    @classmethod
    def get_sub_agent_prompt(cls) -> ChatPromptTemplate:
        """获取子 Agent Prompt 模板。"""
        return cls.get("sub_agent")

    @classmethod
    def get_reviewer_prompt(cls) -> ChatPromptTemplate:
        """获取 Reviewer Prompt 模板。"""
        return cls.get("reviewer")

    @classmethod
    def init_defaults(cls) -> None:
        """注册所有默认 Prompt 模板。在应用启动时调用。"""
        cls.register("planner", PLANNER_PROMPT)
        cls.register("replanner", REPLANNER_PROMPT)
        cls.register("executor", EXECUTOR_PROMPT)
        cls.register("reflection", REFLECTION_PROMPT)
        cls.register("supervisor", SUPERVISOR_PROMPT)
        cls.register("sub_agent", SUB_AGENT_PROMPT)
        cls.register("reviewer", REVIEWER_PROMPT)
