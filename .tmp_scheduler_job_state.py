import os, asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

url = os.getenv('DATABASE_URL', 'postgresql://fover_user:242374@localhost:5432/fover_db')
if url.startswith('postgres://'):
    url = url.replace('postgres://', 'postgresql+asyncpg://', 1)
elif url.startswith('postgresql://'):
    url = url.replace('postgresql://', 'postgresql+asyncpg://', 1)

async def main():
    eng = create_async_engine(url, echo=False, future=True)
    async with eng.connect() as conn:
        rows = (await conn.execute(text("SELECT job_id, name, next_run_time FROM apscheduler_jobs ORDER BY job_id"))).fetchall()
        print(rows)
    await eng.dispose()

asyncio.run(main())
