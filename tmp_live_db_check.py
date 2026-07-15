import asyncio
import os

os.environ['DATABASE_URL'] = 'postgresql://fover_user:242374@localhost:5432/fover_db'
os.environ['JWT_SECRET_KEY'] = 'dev-secret'
os.environ['GOOGLE_CLIENT_ID'] = 'dummy'
os.environ['REDIS_URL'] = 'redis://localhost:6379/0'
os.environ['FOOTBALL_API_KEY'] = 'dummy'
os.environ['API_KEY'] = 'dummy'

from app.db import async_session
from sqlalchemy import text

async def main():
    async with async_session() as db:
        rows = await db.execute(text('select count(*) as c from matches'))
        print('matches_count', rows.scalar())
        rows = await db.execute(text('select count(*) as c from teams'))
        print('teams_count', rows.scalar())
        rows = await db.execute(text('select team_id, current_league_id, current_season, name from teams order by team_id desc limit 10'))
        for row in rows.fetchall():
            print(row)

asyncio.run(main())
