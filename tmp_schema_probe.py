import asyncio
from app.db import engine
from sqlalchemy import text
async def main():
    async with engine.begin() as conn:
        for table in ['matches','standings','teams']:
            result = await conn.execute(text('SELECT indexname, indexdef FROM pg_indexes WHERE tablename = :table ORDER BY indexname'), {'table': table})
            print('--', table, '--')
            for row in result.fetchall():
                print(row)
            print()
asyncio.run(main())