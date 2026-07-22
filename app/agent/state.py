"""
LangGraph Agent 状态定义。
定义全局 AgentState，在所有节点之间共享和传递。
"""

from __future__ import annotations

from operator import add
from typing import Annotated, Any, Optional, TypedDict

from langgraph.graph.message import add_messages


class AgentState(TypedDict):
    """
    LangGraph 全局状态定义。

    每个节点读取并修改此状态，LangGraph 负责状态的持久化和传递。
    使用 Annotated[list, add] 实现列表字段的自动累加。
    """

    # ---- 输入 ----
    goal: str  # 用户原始目标
    context: Optional[str]  # 附加上下文

    # ---- 计划 ----
    plan: Optional[dict[str, Any]]  # 当前计划（JSON 结构）
    plan_version: int  # 计划版本号

    # ---- 执行 ----
    current_task_index: int  # 当前执行的子任务索引
    task_results: Annotated[list[dict[str, Any]], add]  # 已完成子任务的结果（累加）

    # ---- 反思 ----
    reflection_result: Optional[dict[str, Any]]  # 反思评估结果

    # ---- 控制流 ----
    should_replan: bool  # 是否需要重新规划
    iteration_count: int  # 当前迭代次数（防无限循环）
    final_result: Optional[str]  # 最终结果

    # ---- 元数据 ----
    task_id: str  # 任务 ID
    messages: Annotated[list, add_messages]  # LLM 消息历史
    errors: Annotated[list[str], add]  # 错误记录
