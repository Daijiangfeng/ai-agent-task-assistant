"""
SQL 查询工具。
连接 SQLite 沙箱库，仅允许只读 SELECT 查询，首次运行自动初始化示例数据。
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from app.config.logging import get_logger
from app.config.settings import Settings, get_settings
from app.tools.base import BaseTool, ToolInput, ToolOutput

logger = get_logger(__name__)

# 禁止的 DML/DDL 关键字（大写匹配）
_FORBIDDEN_KEYWORDS = {
    "INSERT",
    "UPDATE",
    "DELETE",
    "DROP",
    "ALTER",
    "CREATE",
    "REPLACE",
    "TRUNCATE",
    "ATTACH",
    "DETACH",
    "PRAGMA",
    "VACUUM",
    "GRANT",
    "REVOKE",
}

# 单次查询最大返回行数
_MAX_ROWS = 100


class SQLQueryTool(BaseTool):
    """
    SQL 查询工具（SQLite 沙箱）。

    安全策略：
    - 仅允许以 SELECT 或 WITH 开头的只读查询
    - 禁止分号多语句、禁止 DML/DDL 关键字
    - 结果最多返回 100 行
    首次运行时自动创建示例表 employees / orders 并填充数据。
    """

    def __init__(self, settings: Settings | None = None):
        self._settings = settings or get_settings()
        self._db_path = self._settings.sqlite_sandbox_path
        self._initialized = False

    @property
    def name(self) -> str:
        return "sql_query"

    @property
    def description(self) -> str:
        return (
            "在示例数据库上执行只读 SQL 查询（仅支持 SELECT）。"
            "可用表：employees(id, name, department, salary, hire_date)、"
            "orders(id, employee_id, amount, order_date, status)。"
            "适合数据统计、筛选、聚合查询。"
        )

    def _ensure_sandbox(self) -> None:
        """确保沙箱库存在并初始化示例数据。"""
        if self._initialized:
            return
        path = Path(self._db_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        conn = sqlite3.connect(self._db_path)
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name='employees'"
            )
            if cur.fetchone() is None:
                self._init_sample_data(conn)
            conn.commit()
        finally:
            conn.close()
        self._initialized = True

    @staticmethod
    def _init_sample_data(conn: sqlite3.Connection) -> None:
        """创建示例表并填充数据。"""
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE employees (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                department TEXT NOT NULL,
                salary REAL NOT NULL,
                hire_date TEXT NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE orders (
                id INTEGER PRIMARY KEY,
                employee_id INTEGER NOT NULL,
                amount REAL NOT NULL,
                order_date TEXT NOT NULL,
                status TEXT NOT NULL
            )
            """
        )
        employees = [
            (1, "张三", "研发部", 25000, "2021-03-15"),
            (2, "李四", "研发部", 28000, "2020-07-01"),
            (3, "王五", "销售部", 18000, "2022-01-10"),
            (4, "赵六", "销售部", 22000, "2019-11-20"),
            (5, "钱七", "市场部", 20000, "2023-05-05"),
        ]
        orders = [
            (1, 3, 12000, "2023-06-01", "completed"),
            (2, 3, 8000, "2023-06-15", "completed"),
            (3, 4, 15000, "2023-07-02", "pending"),
            (4, 4, 9500, "2023-07-20", "completed"),
            (5, 5, 6000, "2023-08-01", "cancelled"),
        ]
        cur.executemany(
            "INSERT INTO employees VALUES (?, ?, ?, ?, ?)", employees
        )
        cur.executemany("INSERT INTO orders VALUES (?, ?, ?, ?, ?)", orders)

    @staticmethod
    def _validate(sql: str) -> str | None:
        """
        校验 SQL 安全性。

        Returns:
            通过返回 None，否则返回错误消息。
        """
        stripped = sql.strip().rstrip(";").strip()
        if not stripped:
            return "SQL 语句为空"

        # 禁止多语句
        if ";" in stripped:
            return "禁止执行多条语句"

        lowered = stripped.lower()
        if not (lowered.startswith("select") or lowered.startswith("with")):
            return "仅允许 SELECT 查询"

        # 关键字白名单校验（按单词边界匹配）
        tokens = set(re.findall(r"[A-Za-z_]+", stripped.upper()))
        forbidden = tokens & _FORBIDDEN_KEYWORDS
        if forbidden:
            return f"禁止使用关键字: {', '.join(sorted(forbidden))}"

        return None

    async def execute(self, input: ToolInput) -> ToolOutput:
        """
        执行只读 SQL 查询。

        Args:
            input: query 为 SQL 语句。

        Returns:
            ToolOutput：成功时 data 为结果行列表。
        """
        sql = input.query.strip()
        error = self._validate(sql)
        if error:
            return ToolOutput(success=False, error=error)

        try:
            self._ensure_sandbox()
            conn = sqlite3.connect(self._db_path)
            try:
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                cur.execute(sql.rstrip(";"))
                rows = cur.fetchmany(_MAX_ROWS)
                data = [dict(row) for row in rows]
            finally:
                conn.close()

            return ToolOutput(
                success=True,
                data={"rows": data, "row_count": len(data)},
            )

        except Exception as e:
            logger.warning("SQL 查询失败", error=str(e), sql=sql)
            return ToolOutput(success=False, error=f"查询失败: {str(e)}")
