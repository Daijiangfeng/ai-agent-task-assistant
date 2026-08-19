"""清空 PostgreSQL 中的全部任务记录（使用应用自身配置连接，不暴露密钥）。"""
import asyncio

from sqlalchemy import func, select, text

from app.config.database import get_async_engine


async def main() -> None:
    engine = get_async_engine()
    try:
        async with engine.connect() as conn:
            count_stmt = select(func.count()).select_from(text("tasks"))
            total = (await conn.execute(count_stmt)).scalar_one()
            print(f"BEFORE={total}")
            await conn.execute(text("DELETE FROM tasks"))
            await conn.commit()
            after = (await conn.execute(count_stmt)).scalar_one()
            print(f"AFTER={after}")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
