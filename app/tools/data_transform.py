"""
Reason — 数据处理工具（data.transform）。

提供通用的数据变换能力，避免把业务逻辑硬编码进工具：
- 输入：rows（记录列表）或 text（CSV 文本，可选 headers）
- 操作：filter / map (project) / sort / aggregate / limit
- 全部无副作用（纯函数式），仅作用于内存数据。

安全：不执行任意代码（过滤/投影表达式用受限白名单字段名），纯数据转换。
"""

from __future__ import annotations

import csv
import io
import json
from typing import Any

from app.config.logging import get_logger
from app.tools.base import BaseTool, ToolInput, ToolOutput
from app.tools.errors import ValidationError
from app.tools.schema import ExecutionMode, ToolCategory
from app.tools.security import CATEGORY_SYSTEM, ToolContext

logger = get_logger(__name__)


def _coerce_rows(payload: Any) -> list[dict[str, Any]]:
    """将 payload 规范为 list[dict]。支持 list[dict] / list[list]（需表头）。"""
    if isinstance(payload, list):
        rows: list[dict[str, Any]] = []
        for item in payload:
            if isinstance(item, dict):
                rows.append(dict(item))
            else:
                raise ValidationError("rows 数据应为对象（dict）列表")
        return rows
    raise ValidationError("数据既不是 rows 也不是可解析的文本")


class DataTransformTool(BaseTool):
    """
    数据处理工具。

    入参（parameters）：
    - rows: 可选，记录列表（list[dict]）
    - text: 可选，CSV/JSON 文本
    - operation: 必填，filter|map|sort|aggregate|limit
    - field: 目标字段名（filter/map/sort/aggregate 用）
    - operator: filter 用（eq|ne|gt|gte|lt|lte|in|contains）
    - value: filter 比较值
    - dtype: aggregate 用（count|sum|avg|min|max）
    - limit: limit 数量 / sort 用 order=asc|desc
    """

    category: str = CATEGORY_SYSTEM
    runtime_category: ToolCategory = ToolCategory.REASON
    execution_mode: ExecutionMode = ExecutionMode.SYNC
    timeout: float = 10.0
    permissions: frozenset[str] = frozenset({"reason:data"})
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "rows": {"type": "array", "description": "记录列表（list[dict]）"},
            "operation": {
                "type": "string",
                "enum": ["filter", "map", "sort", "aggregate", "limit"],
                "description": "执行的操作",
            },
        },
        "required": ["operation"],
    }

    @property
    def name(self) -> str:
        return "data.transform"

    @property
    def description(self) -> str:
        return (
            "对记录数据执行通用变换：filter/map/sort/aggregate/limit。"
            "传入 rows（list[dict]）或 CSV 文本，返回变换后的数据。"
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
        operation = (params.get("operation") or "").lower()
        if operation not in ("filter", "map", "sort", "aggregate", "limit"):
            return ToolOutput(success=False, error=f"不支持的 operation: {operation}")

        rows = self._load_rows(params)
        try:
            result = self._apply(operation, rows, params)
        except ValidationError as e:
            return ToolOutput(success=False, error=str(e))
        return ToolOutput(
            success=True,
            data={"rows": result, "row_count": len(result)},
        )

    def _load_rows(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        if params.get("rows") is not None:
            return _coerce_rows(params["rows"])
        text = params.get("text")
        if not text:
            raise ValidationError("缺少数据源：需提供 rows 或 text")
        try:
            if str(text).lstrip().startswith(("[", "{")):
                data = json.loads(str(text))
                return _coerce_rows(data)
        except json.JSONDecodeError:
            pass
        # CSV 文本
        headers = params.get("headers")
        sniff = io.StringIO(str(text))
        reader = csv.reader(sniff)
        parsed = list(reader)
        if not parsed:
            raise ValidationError("CSV 内容为空")
        if isinstance(headers, list) and headers:
            header_row = [str(h) for h in headers]
            data_rows = parsed
        else:
            header_row = parsed[0]
            data_rows = parsed[1:]

        def _to_typed(v: str) -> Any:
            v = v.strip()
            try:
                return int(v)
            except ValueError:
                pass
            try:
                return float(v)
            except ValueError:
                pass
            return v

        return [
            {header_row[i]: (_to_typed(r[i]) if i < len(r) else None)
             for i in range(len(header_row))}
            for r in data_rows
        ]

    def _apply(
        self, op: str, rows: list[dict[str, Any]], params: dict[str, Any]
    ) -> list[dict[str, Any]]:
        field = params.get("field")
        if op in ("sort",) and not field:
            raise ValidationError("sort 需要 field")
        if op == "filter":
            return self._filter(rows, field, params)
        if op == "map":
            if not field:
                raise ValidationError("map 需要 field")
            new_field = params.get("as", field)
            return [{**row, new_field: row.get(field)} for row in rows]
        if op == "aggregate":
            dtype = (params.get("dtype") or "count").lower()
            values = [row.get(field) for row in rows] if field else rows
            return [self._aggregate(dtype, values, field)]
        if op == "sort":
            order = (params.get("order") or "asc").lower()
            reverse = order == "desc"
            return sorted(rows, key=lambda r: (r.get(field) is None, r.get(field)), reverse=reverse)
        if op == "limit":
            try:
                n = int(params.get("limit", params.get("n", 10)))
            except (TypeError, ValueError):
                n = 10
            return rows[: max(0, n)]
        return rows

    def _filter(
        self, rows: list[dict[str, Any]], field: Any, params: dict[str, Any]
    ) -> list[dict[str, Any]]:
        if not field:
            raise ValidationError("filter 需要 field")
        operator = (params.get("operator") or "eq").lower()
        value = params.get("value")
        out: list[dict[str, Any]] = []
        for row in rows:
            actual = row.get(field)
            if operator == "eq":
                match = actual == value
            elif operator == "ne":
                match = actual != value
            elif operator == "gt":
                match = self._cmp(actual, value) > 0
            elif operator == "gte":
                match = self._cmp(actual, value) >= 0
            elif operator == "lt":
                match = self._cmp(actual, value) < 0
            elif operator == "lte":
                match = self._cmp(actual, value) <= 0
            elif operator == "in":
                match = actual in (value or [])
            elif operator == "contains":
                match = (
                    value in actual
                    if isinstance(actual, (str, list, dict, tuple)) and value is not None
                    else False
                )
            else:
                raise ValidationError(f"不支持的 operator: {operator}")
            if match:
                out.append(row)
        return out

    @staticmethod
    def _cmp(a: Any, b: Any) -> int:
        try:
            return 0 if a == b else (1 if a > b else -1)
        except TypeError:
            return (a is None) - (b is None)

    def _aggregate(
        self, dtype: str, values: list[Any], field: Any
    ) -> dict[str, Any]:
        number_values = [
            v for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)
        ]
        result: Any
        if dtype == "count":
            result = len(values)
        elif dtype == "sum":
            result = sum(number_values)
        elif dtype == "avg":
            result = round(sum(number_values) / len(number_values), 6) if number_values else None
        elif dtype == "min":
            out = [v for v in number_values]
            result = min(out) if out else None
        elif dtype == "max":
            out = [v for v in number_values]
            result = max(out) if out else None
        else:
            raise ValidationError(f"不支持 aggregate dtype: {dtype}")
        return {"field": field, "dtype": dtype, "result": result}
