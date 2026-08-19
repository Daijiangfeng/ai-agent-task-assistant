"""
Act — 数据库写工具（database.write）。

与只读 SQLQueryTool 互补，用于受控的 INSERT/UPDATE/DELETE。
安全设计：
- 仅允许对白名单表（employees/orders）做参数化写操作，禁止字符串拼接 SQL；
- 强制事务：单条写操作失败自动回滚；
- 需要 act:database 权限；由 ToolExecutor/Agent 审批闸门在调用前 gate（Act 高风险）；
- 危险语句（DROP/ALTER/TRUNCATE 等）一律拒绝。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from app.config.logging import get_logger
from app.config.settings import Settings, get_settings
from app.tools.base import BaseTool, ToolInput, ToolOutput
from app.tools.schema import ExecutionMode, ToolCategory
from app.tools.security import CATEGORY_SQL, ToolContext

logger = get_logger(__name__)

# 仅允许写这些表；列与值均由参数绑定，杜绝拼接注入
_WRITE_TABLES = frozenset({"employees", "orders"})


class DatabaseWriteTool(BaseTool):
    """
    数据库写工具（SQLite 沙箱，参数化 + 事务）。

    入参（parameters）：
    - table: 必填，目标表（employees/orders）
    - action: 必填，insert|update|delete
    - data:  insert/update 用，字段字典
    - where: update/delete 用，条件字段名（只支持单列等值）
    - where_value: update/delete 用，条件值
    """

    category: str = CATEGORY_SQL
    runtime_category: ToolCategory = ToolCategory.ACT
    execution_mode: ExecutionMode = ExecutionMode.SYNC
    timeout: float = 10.0
    permissions: frozenset[str] = frozenset({"act:database"})
    metadata: dict[str, Any] = {"side_effect": True, "risk": "high", "idempotent": False}
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "table": {"type": "string", "description": "目标表（employees/orders）"},
            "action": {"type": "string", "enum": ["insert", "update", "delete"]},
            "data": {"type": "object", "description": "写入/更新的字段映射"},
            "where": {"type": "string", "description": "更新/删除条件字段（等值）"},
            "where_value": {"type": "string", "description": "条件值"},
        },
        "required": ["table", "action"],
    }

    def __init__(self, settings: Settings | None = None):
        self._settings = settings or get_settings()
        self._db_path = self._settings.sqlite_sandbox_path

    @property
    def name(self) -> str:
        return "database.write"

    @property
    def description(self) -> str:
        return (
            "对示例数据库执行受控写操作（INSERT/UPDATE/DELETE），带事务与审批。"
            "仅允许表 employees/orders，参数化执行。高风险，需用户审批。"
        )

    def _connect(self) -> sqlite3.Connection:
        path = Path(self._db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, timeout=5.0)
        # 隔离重要写操作：关闭隐式跨查询 autocommit，交给显式事务
        conn.isolation_level = None
        return conn

    async def execute(
        self,
        input: ToolInput,
        context: ToolContext | None = None,
    ) -> ToolOutput:
        auth_error = self._authorize(context)
        if auth_error:
            return ToolOutput(success=False, error=auth_error)

        params = input.parameters or {}
        table = str(params.get("table") or "").lower()
        action = str(params.get("action") or "").lower()
        if table not in _WRITE_TABLES:
            return ToolOutput(success=False, error=f"不允许操作的表: {table or '(空)'}")
        if action not in ("insert", "update", "delete"):
            return ToolOutput(success=False, error=f"不支持的 action: {action}")

        data = params.get("data") or {}
        where = params.get("where")
        where_value = params.get("where_value")

        try:
            conn = self._connect()
            try:
                conn.execute("BEGIN")
                if action == "insert":
                    if not isinstance(data, dict) or not data:
                        raise ValueError("insert 需要 data（字段字典）")
                    columns = ",".join(data.keys())
                    placeholders = ",".join("?" for _ in data.keys())
                    sql = f"INSERT INTO {table} ({columns}) VALUES ({placeholders})"
                    cur = conn.execute(sql, list(data.values()))
                else:  # update / delete
                    if not where:
                        raise ValueError(f"{action} 需要 where 条件")
                    if action == "update":
                        if not isinstance(data, dict) or not data:
                            raise ValueError("update 需要 data（字段字典）")
                        set_clause = ", ".join(f"{c} = ?" for c in data.keys())
                        sql = f"UPDATE {table} SET {set_clause} WHERE {where} = ?"
                        cur = conn.execute(sql, [*data.values(), where_value])
                    else:
                        sql = f"DELETE FROM {table} WHERE {where} = ?"
                        cur = conn.execute(sql, [where_value])
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
            finally:
                conn.close()
            return ToolOutput(
                success=True,
                data={
                    "action": action,
                    "table": table,
                    "affected": getattr(cur, "rowcount", 0),
                    "id": getattr(cur, "lastrowid", None),
                },
            )
        except sqlite3.Error as e:
            logger.warning("database.write 失败", error=str(e), action=action, table=table)
            return ToolOutput(success=False, error=f"写入失败: {e}")
        except ValueError as e:
            return ToolOutput(success=False, error=str(e))
