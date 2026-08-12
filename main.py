"""
FastAPI 应用入口（薄封装）。

应用工厂与生命周期管理位于 app.main；此处仅暴露 app 对象，
保持 `uvicorn main:app` 与 `from main import app` 的兼容入口。
"""

from app.main import app  # noqa: F401
