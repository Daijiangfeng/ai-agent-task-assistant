"""Alembic 迁移环境。

- 目标元数据：``app.models.task_record.Base``（业务表结构的单一来源，
  与运行期 ``create_all`` 引导共用同一 metadata，双路径结构一致）；
- 数据库 URL 解析优先级：alembic.ini / 命令行显式配置 > 应用 Settings
  （PostgreSQL，同步驱动 psycopg）；
- 在已有 ``create_all`` 引导过的库上首次接入 Alembic 时，执行
  ``alembic stamp head`` 标记基线即可（表已存在，无需重复执行基线迁移）。
"""

from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import create_engine

# 项目根目录（migrations/ 的上一级）加入 sys.path，
# 保证 `alembic` CLI 与编程式调用（测试）均可导入 app 包
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config.settings import get_settings  # noqa: E402
from app.models.task_record import Base  # noqa: E402

# Alembic Config 对象（提供 ini 中定义的配置项访问）
config = context.config

# 按 ini 配置初始化日志（存在 logging 段时）
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# 迁移 autogenerate 的比对目标
target_metadata = Base.metadata


def _resolve_url() -> str:
    """解析迁移目标数据库 URL。

    显式配置（alembic.ini / set_main_option / -x 覆盖）优先，
    否则使用应用配置的 PostgreSQL DSN（同步驱动 psycopg）。
    """
    url = (config.get_main_option("sqlalchemy.url") or "").strip()
    if url:
        return url
    dsn = get_settings().postgres_dsn
    # postgres_dsn 形如 postgresql://...，同步引擎显式指定 psycopg（v3）驱动
    if dsn.startswith("postgresql://"):
        dsn = dsn.replace("postgresql://", "postgresql+psycopg://", 1)
    return dsn


def run_migrations_offline() -> None:
    """离线模式：仅生成 SQL 脚本，不连接数据库。"""
    context.configure(
        url=_resolve_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """在线模式：连接数据库并应用迁移。"""
    connectable = create_engine(_resolve_url())
    try:
        with connectable.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                compare_type=True,
            )

            with context.begin_transaction():
                context.run_migrations()
    finally:
        connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
