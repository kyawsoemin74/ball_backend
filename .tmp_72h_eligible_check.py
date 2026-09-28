import os, asyncio, json
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
        stmt = text("""
            SELECT
                m.local_match_id AS match_id,
                m.provider_fixture_id,
                m.status,
                m.match_time,
                m.league_name AS league,
                m.season,
                (SELECT COUNT(*) FROM odds o WHERE o.fixture_id = m.local_match_id) AS current_odds_count
            FROM matches m
            WHERE m.status = 'NS'
              AND m.match_time >= NOW()
              AND m.match_time <= NOW() + INTERVAL '72 hours'
              AND m.provider_fixture_id IS NOT NULL
            ORDER BY m.match_time ASC
            LIMIT 20
        """)
        rows = (await conn.execute(stmt)).fetchall()
        print('Eligible NS matches:', len(rows))
        for r in rows:
            print(json.dumps({
                'match_id': r[0],
                'provider_fixture_id': r[1],
                'league': r[4],
                'season': r[5],
                'match_time': str(r[3]),
                'status': r[2],
                'current_odds_count': r[6],
            }))
    await eng.dispose()

asyncio.run(main())
