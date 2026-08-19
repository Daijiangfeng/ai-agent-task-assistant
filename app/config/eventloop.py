"""
事件循环兼容层。

Windows 默认 ProactorEventLoop 与 psycopg 异步模式（AsyncConnectionPool /
AsyncPostgresSaver）不兼容，会报
"Psycopg cannot use the 'ProactorEventLoop' to run in async mode"。
本模块在进程启动早期将 Windows 事件循环策略切换为 SelectorEventLoop
（asyncpg、asyncio 原生协程均兼容），保证 PostgreSQL 系异步组件可正常工作。
"""

from __future__ import annotations

import asyncio
import sys


def ensure_compatible_event_loop() -> None:
    """确保当前平台使用 psycopg 异步兼容的事件循环策略。"""
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
