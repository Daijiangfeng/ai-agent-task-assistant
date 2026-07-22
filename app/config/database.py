"""
数据库连接配置模块。
提供同步和异步 SQLAlchemy 引擎，以及会话工厂。
"""

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config.settings import get_settings


def get_sync_engine():
    """创建同步 SQLAlchemy 引擎。"""
    settings = get_settings()
    return create_engine(
        settings.postgres_dsn,
        echo=settings.DEBUG,
        pool_size=10,
        max_overflow=20,
    )


def get_async_engine():
    """创建异步 asyncpg 引擎。"""
    settings = get_settings()
    return create_async_engine(
        settings.postgres_async_dsn,
        echo=settings.DEBUG,
        pool_size=10,
        max_overflow=20,
    )


def get_session_factory() -> sessionmaker[Session]:
    """创建同步会话工厂。"""
    engine = get_sync_engine()
    return sessionmaker(bind=engine, expire_on_commit=False)


def get_async_session_factory() -> async_sessionmaker[AsyncSession]:
    """创建异步会话工厂。"""
    engine = get_async_engine()
    return async_sessionmaker(bind=engine, expire_on_commit=False)
