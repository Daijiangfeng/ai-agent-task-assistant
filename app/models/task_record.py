"""
SQLAlchemy ORM 模型：任务持久化记录。

Task 的嵌套结构（plan / subtasks / reflection）以 JSON 列存储，
顶层字段（goal/status/owner/tenant/plan_version...）作为独立列便于
按状态/租户/用户聚合查询。兼容 PostgreSQL 与 SQLite 两种后端。
"""

from __future__ import annotations

from sqlalchemy import JSON, Index, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """ORM 声明基类。"""


class TaskRecord(Base):
    """任务表记录。"""

    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    goal: Mapped[str] = mapped_column(Text)
    context: Mapped[str | None] = mapped_column(Text, nullable=True)
    owner_id: Mapped[str] = mapped_column(String(128), default="anonymous")
    tenant_id: Mapped[str] = mapped_column(String(128), default="default")
    status: Mapped[str] = mapped_column(String(32), default="pending")
    plan: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    subtasks: Mapped[list | None] = mapped_column(JSON, nullable=True)
    reflection: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    plan_version: Mapped[int] = mapped_column(default=1)
    iteration_count: Mapped[int] = mapped_column(default=0)
    execution_mode: Mapped[str | None] = mapped_column(String(32), nullable=True)
    agent_results: Mapped[list | None] = mapped_column(JSON, nullable=True)
    pending_approval: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    approval_history: Mapped[list | None] = mapped_column(JSON, nullable=True)
    final_result: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String(64))
    updated_at: Mapped[str] = mapped_column(String(64))

    __table_args__ = (
        # 多租户常用查询：按租户+所有者列任务
        Index("ix_tasks_owner_tenant", "tenant_id", "owner_id"),
        Index("ix_tasks_status", "status"),
    )
