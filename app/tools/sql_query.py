"""
SQL 查询工具。
连接 SQLite 沙箱库，仅允许只读 SELECT 查询，首次运行自动初始化示例数据。

安全设计（纵深防御，Section 3.1 增强）：
1. 语句级校验：sqlglot AST 解析 + 关键字黑名单，仅允许只读 SELECT/WITH
2. 连接级强制只读：file:...?mode=ro URI + PRAGMA query_only=ON，
   即使语句级校验被绕过也无法写入、加载扩展或 attach 外部库
3. 资源限制：progress handler 执行时间/指令数预算、setlimit 单值长度上限、
   返回行数上限，防止大查询 DoS（CROSS JOIN / randomblob 内存炸弹）
4. 字段级权限：表/列访问白名单，禁止查询未授权表与敏感列
"""

from __future__ import annotations

import re
import sqlite3
import time
from pathlib import Path

from app.config.logging import get_logger
from app.config.settings import Settings, get_settings
from app.tools.base import BaseTool, ToolInput, ToolOutput
from app.tools.security import CATEGORY_SQL, ToolContext

logger = get_logger(__name__)

# 禁止的 DML/DDL/危险关键字（大写匹配）
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
    "EXEC",
    "EXECUTE",
    "LOAD",  # load_extension(...)
    "EXTENSION",
    "REINDEX",
}

# 禁止的 SQLite 危险函数（AST 层检查）
_FORBIDDEN_FUNCTIONS = frozenset(
    {"load_extension", "randomblob", "zeroblob", "writefile", "readfile"}
)

# 单次查询最大返回行数
_MAX_ROWS = 100
# 单次查询最大执行时间（毫秒）
_MAX_QUERY_MS = 5000
# 单次查询最大虚拟机指令数（防大查询 DoS）
_MAX_VM_OPS = 2_000_000
# progress handler 回调步长（虚拟机指令数）
_PROGRESS_STEP = 50_000
# 单值最大长度（防 randomblob/zeroblob 内存炸弹）
_MAX_VALUE_LENGTH = 8 * 1024 * 1024
# SQL 语句最大长度
_MAX_SQL_LENGTH = 16 * 1024

# 表/列访问白名单：键为表名，值为允许访问的列集合（未列出的表一律禁止）
_TABLE_COLUMNS: dict[str, frozenset[str]] = {
    "employees": frozenset({"id", "name", "department", "salary", "hire_date"}),
    "orders": frozenset({"id", "employee_id", "amount", "order_date", "status"}),
}


class _QueryBudget:
    """查询预算：累计虚拟机指令数并检查执行时限，超限则中断查询。"""

    def __init__(self, max_ops: int, max_ms: int):
        self._max_ops = max_ops
        self._deadline = time.monotonic() + max_ms / 1000.0
        self._ops = 0

    def __call__(self) -> int:
        """progress handler 回调；返回非零表示中断查询。"""
        self._ops += _PROGRESS_STEP
        if self._ops > self._max_ops or time.monotonic() > self._deadline:
            return 1
        return 0

    @property
    def exceeded(self) -> bool:
        return self._ops > self._max_ops or time.monotonic() > self._deadline


class SQLQueryTool(BaseTool):
    """
    SQL 查询工具（SQLite 沙箱）。

    安全策略：
    - 仅允许以 SELECT 或 WITH 开头的只读查询（sqlglot AST + 关键字双层校验）
    - 连接强制只读（mode=ro + query_only），即使校验被绕过也无法写入
    - 禁止危险函数（load_extension / randomblob / zeroblob 等）
    - 表/列访问白名单，禁止访问未授权表与敏感列
    - 结果最多返回 100 行，查询超时/超指令预算自动中断
    首次运行时自动创建示例表 employees / orders 并填充数据。
    """

    def __init__(self, settings: Settings | None = None):
        self._settings = settings or get_settings()
        self._db_path = self._settings.sqlite_sandbox_path
        self._initialized = False
        # 已核对过 schema 覆盖的表（SELECT * 场景缓存）
        self._star_verified: set[str] = set()

    category: str = CATEGORY_SQL

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

    def _connect_readonly(self) -> sqlite3.Connection:
        """以强制只读模式连接沙箱库，并施加资源限制。"""
        uri = f"file:{self._db_path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=5.0)
        try:
            conn.execute("PRAGMA query_only = ON")
            conn.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, _MAX_VALUE_LENGTH)
            conn.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, _MAX_SQL_LENGTH)
            conn.setlimit(sqlite3.SQLITE_LIMIT_COMPOUND_SELECT, 100)
            conn.setlimit(sqlite3.SQLITE_LIMIT_EXPR_DEPTH, 50)
        except sqlite3.Error:
            conn.close()
            raise
        return conn

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

    def _validate(self, sql: str) -> str | None:
        """
        校验 SQL 安全性（双层防护：sqlparse/sqlglot 语句类型 + 关键字验证）。

        Returns:
            通过返回 None，否则返回错误消息。
        """
        allow_write = getattr(self._settings, "allow_write_sql", False)

        stripped = sql.strip().rstrip(";").strip()
        if not stripped:
            return "SQL 语句为空"

        # 禁止多语句
        if ";" in stripped:
            return "禁止执行多条语句"

        # Layer 1: AST-based statement type detection (if available)
        try:
            import sqlparse

            parsed = sqlparse.parse(stripped)
            if parsed:
                stmt = parsed[0]
                stmt_type = stmt.get_type()
                if stmt_type and stmt_type.upper() not in ("SELECT", "UNKNOWN", None):
                    if not allow_write:
                        return f"仅允许 SELECT 查询（检测到: {stmt_type}）"
        except ImportError:
            pass  # sqlparse not installed, fall through to regex check

        # Layer 2: keyword-based validation (always runs as defense-in-depth)
        lowered = stripped.lower()
        if not allow_write:
            if not (lowered.startswith("select") or lowered.startswith("with")):
                return "仅允许 SELECT 查询"

        # 关键字白名单校验（按单词边界匹配）
        tokens = set(re.findall(r"[A-Za-z_]+", stripped.upper()))
        forbidden = tokens & _FORBIDDEN_KEYWORDS
        if forbidden and not allow_write:
            return f"禁止使用关键字: {', '.join(sorted(forbidden))}"

        return None

    def _missing_allowed_columns(self, table: str) -> set[str]:
        """返回表中未纳入白名单的实际列（通过 schema 核对）。"""
        try:
            conn = self._connect_readonly()
            try:
                rows = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
                actual = {row[1] for row in rows}
            finally:
                conn.close()
        except sqlite3.Error:
            return set()
        return actual - set(_TABLE_COLUMNS.get(table, frozenset()))

    def _validate_ast(self, sql: str) -> str | None:
        """
        sqlglot AST 校验：危险函数、表/列级权限白名单。

        Returns:
            通过返回 None，否则返回错误消息。
        """
        try:
            import sqlglot
            from sqlglot import exp
        except ImportError:
            return None  # AST 校验不可用，依赖连接级强制只读兜底

        try:
            ast = sqlglot.parse_one(sql, read="sqlite")
        except Exception:
            return "SQL 语法无法解析，已拒绝执行"

        # 禁止的危险函数（load_extension / randomblob / zeroblob 等）
        for node in ast.find_all(exp.Anonymous):
            name = str(getattr(node, "this", "") or "").lower()
            if name in _FORBIDDEN_FUNCTIONS:
                return f"禁止使用危险函数: {name}()"

        # AST 层兜底：PRAGMA 等节点类型
        if any(ast.find_all(exp.Pragma)):
            return "禁止使用 PRAGMA 语句"

        # 表级权限：CTE 名跳过，其余表必须在白名单内
        cte_names = {cte.alias_or_name.lower() for cte in ast.find_all(exp.CTE)}
        alias_map: dict[str, str] = {}
        for table in ast.find_all(exp.Table):
            name = table.name.lower()
            if name in cte_names:
                continue
            if name not in _TABLE_COLUMNS:
                return f"无权访问表: {table.name}"
            alias_map[table.alias_or_name.lower()] = name

        # 列级权限：限定列按别名解析到真实表，未限定列须出现在任一引用表白名单
        referenced_tables = set(alias_map.values())
        for col in ast.find_all(exp.Column):
            col_name = col.name.lower()
            qualifier = (col.table or "").lower()
            if qualifier:
                real = alias_map.get(qualifier, qualifier)
                if col_name not in _TABLE_COLUMNS.get(real, frozenset()):
                    return f"无权访问列: {real}.{col.name}"
            else:
                allowed = any(
                    col_name in _TABLE_COLUMNS.get(t, frozenset())
                    for t in referenced_tables
                )
                if not allowed:
                    return f"无权访问列: {col.name}"

        # SELECT * 场景：核对被引用表的实际列与白名单一致，防止白名单遗漏新增列
        if any(ast.find_all(exp.Star)):
            for real in referenced_tables:
                if real in self._star_verified:
                    continue
                missing = self._missing_allowed_columns(real)
                if missing:
                    return (
                        f"表 {real} 存在未纳入白名单的列: "
                        f"{', '.join(sorted(missing))}"
                    )
                self._star_verified.add(real)

        return None

    async def execute(
        self,
        input: ToolInput,
        context: ToolContext | None = None,
    ) -> ToolOutput:
        """
        执行只读 SQL 查询。

        Args:
            input: query 为 SQL 语句。
            context: 调用者身份上下文（权限矩阵校验）。

        Returns:
            ToolOutput：成功时 data 为结果行列表。
        """
        auth_error = self._authorize(context)
        if auth_error:
            return ToolOutput(success=False, error=auth_error)

        sql = input.query.strip()
        error = self._validate(sql)
        if error:
            return ToolOutput(success=False, error=error)

        error = self._validate_ast(sql)
        if error:
            return ToolOutput(success=False, error=error)

        try:
            self._ensure_sandbox()
            budget = _QueryBudget(_MAX_VM_OPS, _MAX_QUERY_MS)
            conn = self._connect_readonly()
            try:
                conn.set_progress_handler(budget, _PROGRESS_STEP)
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                cur.execute(sql.rstrip(";"))
                rows = cur.fetchmany(_MAX_ROWS)
                data = [dict(row) for row in rows]
            finally:
                conn.set_progress_handler(None, 0)
                conn.close()

            if budget.exceeded:
                return ToolOutput(
                    success=False,
                    error="查询超时或超出指令预算，已终止执行",
                )

            return ToolOutput(
                success=True,
                data={"rows": data, "row_count": len(data)},
            )

        except sqlite3.OperationalError as e:
            if "interrupted" in str(e):
                return ToolOutput(
                    success=False,
                    error="查询超时或超出指令预算，已终止执行",
                )
            logger.warning("SQL 查询失败", error=str(e), sql=sql)
            return ToolOutput(success=False, error=f"查询失败: {str(e)}")

        except Exception as e:
            logger.warning("SQL 查询失败", error=str(e), sql=sql)
            return ToolOutput(success=False, error=f"查询失败: {str(e)}")
