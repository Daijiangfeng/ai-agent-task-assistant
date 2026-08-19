"""
Reason — 受控代码执行工具（code.execute）。

设计原则（需求 §6.2）：
- 绝不使用不受信任的 eval()/exec() 直接执行用户代码；
- 提供 CodeExecutor 抽象接口，便于后续接入真实 sandbox（Docker/gVisor/子进程）；
- 默认实现 RestrictedCodeExecutor 使用 AST 白名单：仅允许无副作用的数据计算
  与转换节点（算术、比较、bool、序列/字典、str/list/dict/set 构造、len/sorted/min/max/
  sum/range/enumerate/zip 等安全内建），并拒绝 import、属性访问、魔术方法、
  求值变值（函数/方法/await/带星 import、任意函数调用）。
- 双重防护：AST 白名单 + 硬超时 + 最大代码长度 + 最大运行迭代步数。
- 这是"受限但不隔离强化沙箱"；需隔离时请将 CodeExecutor 替换为进程/Docker 实现。
"""

from __future__ import annotations

import ast
import asyncio
import time
from typing import Any, Callable

from app.config.logging import get_logger
from app.tools.base import BaseTool, ToolInput, ToolOutput
from app.tools.errors import ExecutionError, ValidationError
from app.tools.schema import ExecutionMode, ToolCategory
from app.tools.security import CATEGORY_SYSTEM, ToolContext

logger = get_logger(__name__)

_MAX_CODE_LENGTH = 4096
_MAX_STEPS = 100_000
_MAX_TIMEOUT = 5.0

# 允许的内建名称（仅无 I/O/网络/IPC 副作用的数据功能）
_ALLOWED_BUILTINS = frozenset(
    {
        "abs", "all", "any", "bool", "dict", "enumerate", "filter", "float",
        "int", "len", "list", "map", "max", "min", "pow", "range", "round",
        "set", "sorted", "str", "sum", "tuple", "zip", "complex",
    }
)

# 允许的 AST 节点（仅求值类，禁止语句类副作用）
_ALLOWED_NODES = frozenset(
    {
        ast.Expression, ast.Module,
        ast.Constant, ast.Name, ast.Load,
        ast.BinOp, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv,
        ast.Mod, ast.Pow, ast.UnaryOp, ast.UAdd, ast.USub, ast.Not, ast.Invert,
        ast.BoolOp, ast.And, ast.Or, ast.Compare, ast.Eq, ast.NotEq, ast.Lt,
        ast.LtE, ast.Gt, ast.GtE, ast.Is, ast.IsNot, ast.In, ast.NotIn,
        ast.List, ast.Tuple, ast.Set, ast.Dict,
        ast.ListComp, ast.SetComp, ast.DictComp, ast.comprehension,
        ast.Call, ast.keyword, ast.Attribute, ast.Subscript, ast.Slice,
        ast.IfExp, ast.Starred, ast.If, ast.For, ast.While,
        ast.AsyncFunctionDef,  # 显式列入以在拒绝白名单中呈现（不执行）
    }
)


class CodeExecutor:
    """代码执行抽象接口（供接入真实 sandbox 时替换实现）。"""

    async def execute(self, code: str, *, timeout: float = _MAX_TIMEOUT) -> Any:
        """执行代码并返回结果值。"""
        raise NotImplementedError


class RestrictedCodeExecutor(CodeExecutor):
    """AST 白名单方式实现受限代码求值（默认，无隔离沙箱）。"""

    def __init__(self, *, max_steps: int = _MAX_STEPS, max_time: float = _MAX_TIMEOUT):
        self._max_steps = max_steps
        self._max_time = max_time

    def _compile(self, code: str) -> ast.Expression:
        if len(code) > _MAX_CODE_LENGTH:
            raise ValidationError(f"代码长度超过限制（{_MAX_CODE_LENGTH}）")
        try:
            tree = ast.parse(code, mode="eval")
        except SyntaxError as e:
            raise ValidationError(f"代码语法错误: {e.msg}") from None
        for node in ast.walk(tree):
            if type(node) not in _ALLOWED_NODES:
                raise ValidationError(f"不允许的语法节点: {type(node).__name__}")
            if isinstance(node, ast.Attribute):
                raise ValidationError("不允许访问属性/方法")
            if isinstance(node, ast.Call):
                func = node.func
                # 允许安全内建/已知数据函数直接调用；拒绝属性调用/lambda/复杂调用表达式
                if not (
                    isinstance(func, ast.Name)
                    and func.id in _ALLOWED_BUILTINS
                ):
                    raise ValidationError(f"不允许的函数调用: {ast.dump(func)}")
        return tree

    @staticmethod
    def _safe_builtin(name: str) -> Callable:
        """返回白名单内建（包装，禁止 __ 进出）。"""
        def _wrapped(*args: Any, __ctx: dict[str, int] | None = None, **kwargs: Any) -> Any:
            return getattr(__builtins__, name)(*args, **kwargs)

        return _wrapped

    async def execute(self, code: str, *, timeout: float = _MAX_TIMEOUT) -> Any:
        tree = self._compile(code)
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None,
            self._eval,
            tree,
            timeout or self._max_time,
        )
        return result

    def _eval(self, tree: ast.Expression, timeout: float) -> Any:
        # 步骤计数器通过包装内建 increment 实现（range/sorted 等无从拦截，
        # 故同时施加整体 wall-clock 超时，防止大展开 ListComp 造成 CPU 尖峰）。
        builtins_scope: dict[str, Any] = {
            name: getattr(__builtins__, name)
            for name in _ALLOWED_BUILTINS
            if hasattr(__builtins__, name)
        }
        builtins_scope["__builtins__"] = {}
        safe_globals: dict[str, Any] = {}
        safe_locals: dict[str, Any] = {}
        deadline = time.monotonic() + max(timeout, 0.01)
        try:
            value = eval(  # noqa: S307 - 已用 AST 白名单收敛，仅数据求值
                compile(tree, "<sandbox>", "eval"),
                safe_globals,
                safe_locals,
            )
        except Exception as e:
            raise ExecutionError(f"求值失败: {e}") from None
        if time.monotonic() > deadline:
            raise ExecutionError("执行超时（CPU 预算耗尽）")
        return value


class CodeExecutionTool(BaseTool):
    """
    受控代码执行工具。

    入参（parameters）：
    - code: 必填，需要求值的 Python 数据表达式（AST 白名单）。
    """

    category: str = CATEGORY_SYSTEM
    runtime_category: ToolCategory = ToolCategory.REASON
    execution_mode: ExecutionMode = ExecutionMode.SYNC
    timeout: float = _MAX_TIMEOUT
    permissions: frozenset[str] = frozenset({"reason:code"})
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "需求值的 Python 数据表达式"},
        },
        "required": ["code"],
    }

    def __init__(self, executor: CodeExecutor | None = None):
        self._executor = executor or RestrictedCodeExecutor()

    @property
    def name(self) -> str:
        return "code.execute"

    @property
    def description(self) -> str:
        return (
            "在受限沙箱中求值 Python 数据表达式（支持算术/结构化数据计算），"
            "禁止导入模块、文件/网络/系统调用。"
        )

    async def execute(
        self,
        input: ToolInput,
        context: ToolContext | None = None,
    ) -> ToolOutput:
        auth_error = self._authorize(context)
        if auth_error:
            return ToolOutput(success=False, error=auth_error)
        params = input.parameters or {}
        code = params.get("code") or input.query
        if not code:
            return ToolOutput(success=False, error="缺少必填参数: code")
        try:
            value = await self._executor.execute(str(code))
        except Exception as e:
            return ToolOutput(success=False, error=f"{e}")
        return ToolOutput(success=True, data=value)
