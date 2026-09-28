import os, json, asyncio
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
        row = (await conn.execute(text("""
            SELECT
                m.local_match_id,
                m.provider_fixture_id,
                m.status,
                m.match_time,
                m.league_name,
                m.season,
                (SELECT COUNT(*) FROM odds o WHERE o.fixture_id = m.local_match_id) AS odds_count,
                (SELECT MAX(o.last_updated) FROM odds o WHERE o.fixture_id = m.local_match_id) AS latest_odds_ts
            FROM matches m
            WHERE m.local_match_id = 1570386
        """))).first())
        print(json.dumps(dict(row._mapping), default=str))
    await eng.dispose()

asyncio.run(main())
