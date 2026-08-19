"""
统一 Tool Runtime 测试。

覆盖：
- 注册/查找/Schema（ToolRegistry + ToolSchema + to_function_schema）
- ToolExecutor 统一执行管线（校验/权限/审批/超时/重试/取消/规范化）
- 五类能力（Observe/Reason/Act/Remember/Interact）均有实现且可发现
- 安全（越权/SSRF/SQL 注入/命令注入/密钥外泄/跨用户记忆）
- 错误收敛与敏感信息保护
"""

from __future__ import annotations

import asyncio

import pytest

from app.memory.short_term import InMemoryShortTermMemory
from app.tools.base import ToolInput, ToolOutput
from app.tools.builtins import register_five_category_tools
from app.tools.code_execution import CodeExecutionTool
from app.tools.context import ToolExecutionContext
from app.tools.data_transform import DataTransformTool
from app.tools.database_write import DatabaseWriteTool
from app.tools.email_tool import EmailTool
from app.tools.errors import (
    ExternalServiceError,
    PermissionDeniedError,
    ToolErrorCode,
    ValidationError,
    normalize_exception,
)
from app.tools.executor import ToolExecutor
from app.tools.http_read import HTTPReadTool
from app.tools.interact_tools import UserMessageTool
from app.tools.memory_tools import MemoryDeleteTool, MemoryGetTool, MemorySetTool
from app.tools.permissions import filter_by_permission
from app.tools.registry import ToolRegistry
from app.tools.schema import ToolCategory, ToolSchema
from app.tools.security import ROLE_ADMIN, ROLE_GUEST, ToolContext

# ---------------------------------------------------------------------------
# 五类能力工具集注册
# ---------------------------------------------------------------------------

def _register(setup_tools=True):
    ToolRegistry.clear()
    register_five_category_tools()
    if setup_tools:
        pass
    return ToolRegistry


@pytest.fixture(autouse=True)
def _clean_registry():
    ToolRegistry.clear()
    yield
    ToolRegistry.clear()


class TestFiveCategories:
    """五类能力均有可发现实现。"""

    @staticmethod
    def _registered() -> dict[str, object]:
        _register()
        return ToolRegistry.get_all()

    def test_category_tools_present(self):
        names = set(self._registered().keys())
        # Observe
        assert "http.get" in names
        # Reason
        assert "code.execute" in names
        assert "data.transform" in names
        # Act
        assert "database.write" in names
        assert "email.send" in names
        assert "http.request" in names
        assert "github.create_pr" in names
        # Remember
        for n in ("memory.get", "memory.set", "memory.search", "memory.delete"):
            assert n in names
        # Interact
        for n in ("user.ask", "user.message", "user.approval"):
            assert n in names

    def test_each_category_has_runtime_category(self):
        registry = self._registered()
        by_cat: dict[str, list[str]] = {}
        for name, tool in registry.items():
            schema = tool.to_schema()
            if tool.name.startswith(
                ("http.", "code.", "data.", "database.", "email.", "github.", "memory.", "user.")
            ):
                by_cat.setdefault(schema.category.value, []).append(name)
        assert by_cat.get("observe")
        assert by_cat.get("reason")
        assert by_cat.get("act")
        assert by_cat.get("remember")
        assert by_cat.get("interact")


class TestSchemaAndRegistry:
    """ToolSchema 生成与注册/查找。"""

    def test_tool_schema_fields(self):
        t = HTTPReadTool()
        s = t.to_schema()
        assert s.name == "http.get"
        assert s.category is ToolCategory.OBSERVE
        assert "observe:http" in s.permissions
        assert "url" in s.input_schema["required"]

    def test_to_function_schema(self):
        s = HTTPReadTool().to_schema().to_function_schema()
        assert s["type"] == "function"
        fn = s["function"]
        assert fn["name"] == "http.get"
        assert fn["parameters"]["required"] == ["url"]

    def test_lookup_and_unregister(self):
        _register()
        assert ToolRegistry.get("data.transform") is not None
        ToolRegistry.unregister("data.transform")
        assert ToolRegistry.get("data.transform") is None

    def test_prevent_duplicate(self):
        # 同名覆盖不抛异常，且旧实例被替换
        a = DataTransformTool()
        b = DataTransformTool()
        ToolRegistry.register(a)
        ToolRegistry.register(b)
        assert ToolRegistry.get("data.transform") is b


class TestExecutorPipeline:
    """ToolExecutor 统一执行管线（校验/权限/审批/超时/重试/取消/规范化）。"""

    def _executor(self):
        _register()
        return ToolExecutor(registry=ToolRegistry)

    @pytest.mark.asyncio
    async def test_unregistered_tool(self):
        out = await self._executor().execute("nope.missing", {})
        assert out.success is False
        assert "未注册" in out.error

    @pytest.mark.asyncio
    async def test_unavailable_tool(self):
        _register()
        out = await ToolExecutor(registry=ToolRegistry).execute(
            "http.request", {"method": "post", "url": "https://example.com/x"}
        )
        assert out.success is False
        assert "不可用" in out.error

    @pytest.mark.asyncio
    async def test_input_validation(self):
        _register()
        with pytest.raises(ValidationError):
            await self._executor().execute("http.get", {"method": "get"})  # 缺 url

    @pytest.mark.asyncio
    async def test_source_validation_reason(self):
        _register()
        out = await self._executor().execute(
            "data.transform", {"operation": "filter", "field": "a"}
        )
        assert out.success is False

    @pytest.mark.asyncio
    async def test_permission_role_matrix_denies_guest_for_network(self):
        _register()
        ctx = ToolExecutionContext(tool_context=ToolContext(role=ROLE_GUEST))
        with pytest.raises(PermissionDeniedError):
            await self._executor().execute("http.get", {"url": "https://x.com"}, ctx)

    @pytest.mark.asyncio
    async def test_permission_role_admin_allows_http(self):
        _register()
        # admin 放行 network 类别：http.get 属于 network，不抛权限异常
        ctx = ToolExecutionContext(tool_context=ToolContext(role=ROLE_ADMIN))
        # 不真实访问网络：SSRF 校验在 URL 校验阶段拦截内网 / 协议，
        # 若权限被误判拒绝，会抛出 PermissionDeniedError。这里命中协议校验分支。
        out = await self._executor().execute(
            "http.get", {"url": "ftp://x.com"}, ctx
        )
        # 若权限放行 -> 进入协议校验失败（success=False）；若权限错误拒绝则抛异常
        assert out.success is False
        assert "协议" in out.error

    @pytest.mark.asyncio
    async def test_granted_permissions_filter(self):
        _register()
        filtered = filter_by_permission(
            [t.to_schema() for t in ToolRegistry.get_all().values()],
            {"observe:http"},
        )
        names = {s.name for s in filtered}
        assert "http.get" in names
        assert "database.write" not in names  # 需 act:database

    @pytest.mark.asyncio
    async def test_timeout(self):
        _register()
        slow = ToolSchema(
            name="slow.tool", description="slow", timeout=0.05
        )
        # 用自定义超时验证 ToolExecutor 超时路径
        class _Slow:
            name = "slow.tool"
            metadata = {}
            available = True
            @property
            def to_schema(self):
                return lambda: slow
            async def execute(self, inp, context=None):
                await asyncio.sleep(5)
        ToolRegistry.register(_Slow())
        # 注意：不能用 self._executor()（会 clear 注册表清掉刚注册的 fake），
        # 直接构造执行器，防止重复注册清理。
        out = await ToolExecutor(registry=ToolRegistry).execute("slow.tool", {}, timeout=0.05)
        assert out.success is False
        assert "超时" in out.error

    @pytest.mark.asyncio
    async def test_retry_for_idempotent(self, monkeypatch):
        _register()
        calls = {"n": 0}
        schema = ToolSchema(
            name="idem.tool", description="idem",
            metadata={"idempotent": True}, timeout=1,
        )

        calls = {"n": 0}

        class _Idem:
            name = "idem.tool"
            metadata = {"idempotent": True}
            available = True

            @property
            def to_schema(self):
                return lambda: schema
            async def execute(self, inp, context=None):
                calls["n"] += 1
                if calls["n"] < 2:
                    raise ExternalServiceError("temporary", retryable=True)
                return ToolOutput(success=True, data="ok")

        ToolRegistry.register(_Idem())
        ex = ToolExecutor(registry=ToolRegistry, max_retries=2, default_timeout=1)
        # 降低退避等待
        await asyncio.sleep(0)
        out = await ex.execute("idem.tool", {})
        assert out.success is True
        assert calls["n"] == 2

    @pytest.mark.asyncio
    async def test_no_retry_for_act(self):
        _register()
        calls = {"n": 0}
        schema = ToolSchema(
            name="act.tool", description="act",
            category=ToolCategory.ACT, metadata={"idempotent": False}, timeout=1,
        )

        class _Act:
            name = "act.tool"
            metadata = {"idempotent": False}
            available = True
            @property
            def to_schema(self):
                return lambda: schema
            async def execute(self, inp, context=None):
                calls["n"] += 1
                raise ExternalServiceError("temporary", retryable=True)

        ToolRegistry.register(_Act())
        out = await ToolExecutor(registry=ToolRegistry, max_retries=2).execute("act.tool", {})
        assert out.success is False
        assert calls["n"] == 1  # 副作用工具不重试

    @pytest.mark.asyncio
    async def test_cancellation(self):
        _register()
        ctx = ToolExecutionContext()
        ctx.cancel()
        out = await self._executor().execute("data.transform", {"operation": "limit"}, ctx)
        assert out.success is False
        assert "取消" in out.error


class TestErrorNormalization:
    """错误收敛与 secret 保护。"""

    def test_validation_code(self):
        err = normalize_exception(ValidationError("bad", tool_name="x"))
        assert err.code is ToolErrorCode.VALIDATION_ERROR
        assert err.retryable is False

    def test_timeout_normalized(self):
        err = normalize_exception(asyncio.TimeoutError(), tool_name="x")
        assert err.code is ToolErrorCode.TIMEOUT_ERROR
        assert err.retryable is True

    def test_generic_maps_execution(self):
        err = normalize_exception(RuntimeError("boom"), tool_name="x")
        assert err.code is ToolErrorCode.EXECUTION_ERROR

    def test_secret_not_leaked(self):
        # 底层异常含 token，收敛后 message 不得泄露
        err = normalize_exception(
            RuntimeError("password=abc token=xyz123"), tool_name="x"
        )
        assert "abc" not in err.message
        assert "xyz" not in err.message

    def test_transient_hint_retryable(self):
        err = normalize_exception(RuntimeError("connection refused"), tool_name="x")
        assert err.retryable is True


# ---------------------------------------------------------------------------
# 工具级单测
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestObserveTools:
    """Observe 类工具。"""

    async def test_http_read_blocks_ssrf(self):
        tool = HTTPReadTool()
        out = await tool.execute(
            ToolInput(parameters={"url": "http://127.0.0.1/admin"})
        )
        assert out.success is False
        assert "内网" in out.error or "localhost" in out.error

    async def test_http_read_blocks_bad_scheme(self):
        tool = HTTPReadTool()
        out = await tool.execute(ToolInput(parameters={"url": "file:///etc/passwd"}))
        assert out.success is False
        assert "协议" in out.error


@pytest.mark.asyncio
class TestReasonTools:
    """Reason 类工具。"""

    async def test_code_execute_safe(self):
        tool = CodeExecutionTool()
        out = await tool.execute(ToolInput(parameters={"code": "2 + 3 * 4"}))
        assert out.success is True

    async def test_code_execute_blocks_import(self):
        tool = CodeExecutionTool()
        out = await tool.execute(
            ToolInput(parameters={"code": "import os; os.popen('id').read()"})
        )
        assert out.success is False

    async def test_code_execute_blocks_attribute(self):
        tool = CodeExecutionTool()
        out = await tool.execute(
            ToolInput(parameters={"code": "__import__('os').system('id')"})
        )
        assert out.success is False

    async def test_data_transform_aggregate(self):
        tool = DataTransformTool()
        out = await tool.execute(
            ToolInput(
                parameters={
                    "operation": "aggregate",
                    "dtype": "sum",
                    "field": "amount",
                    "rows": [{"amount": 1}, {"amount": 2}, {"amount": 3}],
                }
            )
        )
        assert out.success is True
        assert out.data["rows"][0]["result"] == 6

    async def test_data_transform_invalid_op(self):
        tool = DataTransformTool()
        out = await tool.execute(ToolInput(parameters={"operation": "sql"}))
        assert out.success is False


@pytest.mark.asyncio
class TestActTools:
    """Act 类工具（副作用）。"""

    def _db_tool(self, tmp_path) -> DatabaseWriteTool:
        import sqlite3

        from app.config.settings import Settings

        db_path = tmp_path / "w.db"
        # 在独立沙箱库中预置白名单表，供写测试使用
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE employees (id INTEGER PRIMARY KEY, name TEXT)")
        conn.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY, amount REAL)")
        conn.commit()
        conn.close()
        tool = DatabaseWriteTool(Settings(SQLITE_SANDBOX_PATH=str(db_path)))
        return tool

    async def test_db_write_sql_injection_blocked(self, tmp_path):
        tool = self._db_tool(tmp_path)
        # 参数化：注入字符串仅作值，不改变语句结构
        out = await tool.execute(
            ToolInput(
                parameters={
                    "table": "employees",
                    "action": "update",
                    "data": {"name": "x' OR 1=1 --"},
                    "where": "id",
                    "where_value": 1,
                }
            )
        )
        assert out.success is True

    async def test_db_write_unknown_table_rejected(self, tmp_path):
        tool = self._db_tool(tmp_path)
        out = await tool.execute(
            ToolInput(
                parameters={
                    "table": "users_secret",
                    "action": "delete",
                    "where": "id",
                    "where_value": 1,
                }
            )
        )
        assert out.success is False
        assert "不允许" in out.error

    async def test_email_send_mock(self):
        tool = EmailTool()
        ctx = ToolContext(role=ROLE_ADMIN)
        out = await tool.execute(
            ToolInput(parameters={"to": "a@b.com", "subject": "hi", "body": "x"}),
            context=ctx,
        )
        assert out.success is True
        assert tool._provider.sent


@pytest.mark.asyncio
class TestRememberTools:
    """Remember 类工具（跨用户隔离）。"""

    def _mem(self):
        return InMemoryShortTermMemory()

    async def test_memory_set_get(self):
        mem = self._mem()
        tool = MemorySetTool(memory=mem)
        ctx = ToolContext(role=ROLE_ADMIN, user_id="u1", tenant_id="t1")
        out = await tool.execute(
            ToolInput(parameters={"key": "pref", "value": "dark"}),
            context=ctx,
        )
        assert out.success is True
        got = await MemoryGetTool(memory=mem).execute(
            ToolInput(parameters={"key": "pref"}),
            context=ToolContext(role=ROLE_ADMIN, user_id="u1", tenant_id="t1"),
        )
        assert got.success is True
        assert got.data["value"] == "dark"

    async def test_memory_cross_user_isolation(self):
        mem = self._mem()
        set_tool = MemorySetTool(memory=mem)
        await set_tool.execute(
            ToolInput(parameters={"key": "pref", "value": "secret"}),
            context=ToolContext(role=ROLE_ADMIN, user_id="u1", tenant_id="t1"),
        )
        got = await MemoryGetTool(memory=mem).execute(
            ToolInput(parameters={"key": "pref"}),
            context=ToolContext(role=ROLE_ADMIN, user_id="u2", tenant_id="t1"),
        )
        assert got.success is False  # 跨用户读不到

    async def test_memory_delete(self):
        mem = self._mem()
        await MemorySetTool(memory=mem).execute(
            ToolInput(parameters={"key": "k", "value": "v"}),
            context=ToolContext(user_id="u", tenant_id="t", role=ROLE_ADMIN),
        )
        await MemoryDeleteTool(memory=mem).execute(
            ToolInput(parameters={"key": "k"}),
            context=ToolContext(user_id="u", tenant_id="t", role=ROLE_ADMIN),
        )
        got = await MemoryGetTool(memory=mem).execute(
            ToolInput(parameters={"key": "k"}),
            context=ToolContext(user_id="u", tenant_id="t", role=ROLE_ADMIN),
        )
        assert got.success is False


@pytest.mark.asyncio
class TestInteractTools:
    """Interact 类工具。"""

    async def test_user_message(self):
        tool = UserMessageTool()
        out = await tool.execute(
            ToolInput(parameters={"text": "你好"}), context=ToolContext(role=ROLE_ADMIN)
        )
        assert out.success is True

    async def test_user_requires_channel(self):
        from app.tools.interact_tools import UserAskTool

        out = await UserAskTool().execute(
            ToolInput(parameters={"question": "继续?"}), context=ToolContext(role=ROLE_ADMIN)
        )
        assert out.success is False
        assert "APPROVAL_REQUIRED" in out.error


# ---------------------------------------------------------------------------
# 集成：Executor 完整管线（Reason + Remember）
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestExecutorIntegration:
    async def test_full_pipeline_reason(self):
        _register()
        ex = ToolExecutor(registry=ToolRegistry)
        ctx = ToolExecutionContext(tool_context=ToolContext(role=ROLE_ADMIN))
        out = await ex.execute(
            "data.transform",
            {"operation": "aggregate", "dtype": "sum", "field": "x", "rows": [{"x": 1}, {"x": 2}]},
            ctx,
        )
        assert out.success is True
        assert out.data["rows"][0]["result"] == 3

    async def test_full_pipeline_remember(self):
        out_mem = InMemoryShortTermMemory()
        ToolRegistry.register(MemorySetTool(memory=out_mem))
        ex = ToolExecutor(registry=ToolRegistry)
        ctx = ToolExecutionContext(tool_context=ToolContext(role=ROLE_ADMIN))
        res = await ex.execute(
            "memory.set", {"key": "k", "value": "v"}, ctx
        )
        assert res.success is True
