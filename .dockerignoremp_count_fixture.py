import os
from sqlalchemy import create_engine, text

url = os.getenv('DATABASE_URL', 'postgresql://fover_user:242374@localhost:5432/fover_db')
print('DATABASE_URL=', url)
sync_url = url.replace('postgresql+asyncpg://', 'postgresql+psycopg2://').replace('postgres://', 'postgresql+psycopg2://')
engine = create_engine(sync_url, future=True)
print('MATCH_ID=1546509')
with engine.connect() as conn:
    tables = [('match_events', 'match_id'), ('match_statistics', 'match_id'), ('match_lineups', 'match_id'), ('odds', 'fixture_id')]
    for table, col in tables:
        try:
            res = conn.execute(text(f'SELECT COUNT(*) FROM {table} WHERE {col}=1546509'))
            print(f'{table}:', res.scalar())
        except Exception as e:
            print(f'{table}: ERROR -> {type(e).__name__}: {e}')
    try:
        res = conn.execute(text('SELECT COUNT(*) FROM match_h2h WHERE h2h_key LIKE :k'), {'k': '%1546509%'})
        print('match_h2h:', res.scalar())
    except Exception as e:
        print('match_h2h: ERROR ->', type(e).__name__, e)
