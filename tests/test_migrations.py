"""Alembic 基线迁移测试。

验证目标：
1. ``upgrade head`` 可在空库上完整执行（离线 SQLite 验证，无需真实 PostgreSQL）；
2. 迁移产物与 ``Base.metadata.create_all`` 产物结构一致——开发环境 create_all
   引导与生产环境 alembic 演进双路径不得漂移；
3. ``downgrade base`` 可完整回滚；
4. 迁移链单一 head（无分叉）。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect

from app.models.task_record import Base

REPO_ROOT = Path(__file__).resolve().parent.parent
# alembic_version 为 Alembic 自身的版本表，不参与业务表结构比对
ALEMBIC_VERSION_TABLE = "alembic_version"


def _alembic_config(db_url: str) -> Config:
    """构造指向指定数据库的 Alembic 配置。"""
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


def _schema_snapshot(engine) -> dict:
    """提取表结构快照：表 -> {列定义} + 索引集合（排除 alembic 版本表）。"""
    inspector = inspect(engine)
    tables: dict[str, dict] = {}
    for table_name in sorted(inspector.get_table_names()):
        if table_name == ALEMBIC_VERSION_TABLE:
            continue
        columns = {
            col["name"]: {
                "type": str(col["type"]),
                "nullable": col["nullable"],
            }
            for col in inspector.get_columns(table_name)
        }
        indexes = {
            (idx["name"], tuple(idx["column_names"]))
            for idx in inspector.get_indexes(table_name)
        }
        pk = tuple(inspector.get_pk_constraint(table_name).get("constrained_columns") or [])
        tables[table_name] = {"columns": columns, "indexes": indexes, "pk": pk}
    return tables


@pytest.fixture
def migrated_engine(tmp_path):
    """通过 Alembic 迁移生成的数据库引擎。"""
    db_path = tmp_path / "migrated.db"
    command.upgrade(_alembic_config(f"sqlite:///{db_path}"), "head")
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def createall_engine(tmp_path):
    """通过 Base.metadata.create_all 生成的数据库引擎。"""
    db_path = tmp_path / "createall.db"
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()


class TestBaselineMigration:
    """基线迁移执行与结构一致性。"""

    def test_upgrade_creates_tasks_table(self, migrated_engine):
        """upgrade head 后 tasks 表存在且列齐全。"""
        snapshot = _schema_snapshot(migrated_engine)
        assert "tasks" in snapshot
        expected_columns = {
            "id", "goal", "context", "owner_id", "tenant_id", "status",
            "plan", "subtasks", "reflection", "plan_version", "iteration_count",
            "execution_mode", "agent_results", "pending_approval",
            "approval_history", "final_result", "error", "created_at",
            "updated_at",
        }
        assert set(snapshot["tasks"]["columns"]) == expected_columns
        # 主键与索引
        assert snapshot["tasks"]["pk"] == ("id",)
        index_names = {name for name, _ in snapshot["tasks"]["indexes"]}
        assert {"ix_tasks_owner_tenant", "ix_tasks_status"} <= index_names

    def test_migration_matches_create_all_schema(
        self, migrated_engine, createall_engine
    ):
        """迁移产物与 create_all 产物结构完全一致（双路径一致性）。"""
        assert _schema_snapshot(migrated_engine) == _schema_snapshot(
            createall_engine
        )

    def test_downgrade_drops_tasks_table(self, tmp_path):
        """downgrade base 后 tasks 表被移除。"""
        db_path = tmp_path / "downgrade.db"
        db_url = f"sqlite:///{db_path}"
        cfg = _alembic_config(db_url)
        command.upgrade(cfg, "head")
        command.downgrade(cfg, "base")
        engine = create_engine(db_url)
        try:
            inspector = inspect(engine)
            tables = {
                t for t in inspector.get_table_names()
                if t != ALEMBIC_VERSION_TABLE
            }
            assert tables == set()
        finally:
            engine.dispose()

    def test_single_head_revision(self):
        """迁移链无分叉，仅一个 head。"""
        script = ScriptDirectory.from_config(_alembic_config("sqlite://"))
        heads = script.get_heads()
        assert len(heads) == 1
        assert heads[0] == "0001_baseline"
