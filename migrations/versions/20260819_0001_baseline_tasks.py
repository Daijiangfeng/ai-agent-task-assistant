"""基线迁移：tasks 表（业务核心表结构快照）

与 app.models.task_record.TaskRecord 的 ORM 定义保持一致。
后续表结构演进请通过 `alembic revision --autogenerate` 生成新迁移，
勿手改本基线；已在 create_all 引导过的存量库首次接入时执行
`alembic stamp head` 标记基线。

Revision ID: 0001_baseline
Revises:
Create Date: 2026-08-19

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001_baseline"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tasks",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("goal", sa.Text(), nullable=False),
        sa.Column("context", sa.Text(), nullable=True),
        sa.Column("owner_id", sa.String(length=128), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("plan", sa.JSON(), nullable=True),
        sa.Column("subtasks", sa.JSON(), nullable=True),
        sa.Column("reflection", sa.JSON(), nullable=True),
        sa.Column("plan_version", sa.Integer(), nullable=False),
        sa.Column("iteration_count", sa.Integer(), nullable=False),
        sa.Column("execution_mode", sa.String(length=32), nullable=True),
        sa.Column("agent_results", sa.JSON(), nullable=True),
        sa.Column("pending_approval", sa.JSON(), nullable=True),
        sa.Column("approval_history", sa.JSON(), nullable=True),
        sa.Column("final_result", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.String(length=64), nullable=False),
        sa.Column("updated_at", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tasks_owner_tenant", "tasks", ["tenant_id", "owner_id"])
    op.create_index("ix_tasks_status", "tasks", ["status"])


def downgrade() -> None:
    op.drop_index("ix_tasks_status", table_name="tasks")
    op.drop_index("ix_tasks_owner_tenant", table_name="tasks")
    op.drop_table("tasks")
