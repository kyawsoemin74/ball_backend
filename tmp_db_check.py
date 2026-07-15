import asyncio
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy import text
from app.core.config import settings

async def main():
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    async with AsyncSession(engine) as session:
        try:
            await session.execute(text("SELECT 1"))
            print('db-ok')
        except Exception as exc:
            print('db-error', exc)
            return
        res = await session.execute(text("SELECT username, role, is_active FROM users LIMIT 10"))
        rows = res.fetchall()
        print(rows)

asyncio.run(main())
