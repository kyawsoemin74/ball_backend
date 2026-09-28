import os
import asyncio
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
        rows = (await conn.execute(text("""
            SELECT
                m.local_match_id,
                m.provider_fixture_id,
                m.status,
                m.match_time,
                m.league_name,
                m.season,
                COALESCE(o.odds_count, 0) AS odds_count
            FROM matches m
            LEFT JOIN (
                SELECT fixture_id, COUNT(*) AS odds_count
                FROM odds
                GROUP BY fixture_id
            ) o ON o.fixture_id = m.local_match_id
            WHERE m.status IN ('NS', 'TBD', 'PST')
              AND m.match_time >= NOW()
              AND m.match_time <= NOW() + INTERVAL '72 hours'
            ORDER BY m.match_time ASC
            LIMIT 10
        """))).fetchall()
        if not rows:
            print('NO_ELIGIBLE_MATCH_FOUND')
            return
        for r in rows:
            print({
                'match_id': r[0],
                'provider_fixture_id': r[1],
                'status': r[2],
                'match_time': str(r[3]),
                'league_name': r[4],
                'season': r[5],
                'odds_count': r[6],
            })
    await eng.dispose()

asyncio.run(main())
