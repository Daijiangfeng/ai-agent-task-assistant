"""配置管理模块。"""

from app.config.eventloop import ensure_compatible_event_loop
from app.config.settings import Settings, get_settings

# 进程启动早期即完成事件循环策略切换（Windows 下 psycopg 异步需要
# SelectorEventLoop），必须在任何 asyncio 事件循环创建之前执行。
ensure_compatible_event_loop()

__all__ = ["Settings", "get_settings"]
