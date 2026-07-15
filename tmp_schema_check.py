import os
import asyncio

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
        cols = await db.execute(text("select column_name from information_schema.columns where table_name='teams' and column_name in ('current_league_id','current_season') order by column_name"))
        print('team_context_columns', [r[0] for r in cols.fetchall()])
        rows = await db.execute(text("select team_id, current_league_id, current_season from teams where team_id=27844"))
        print('team_27844', rows.fetchone())

asyncio.run(main())
