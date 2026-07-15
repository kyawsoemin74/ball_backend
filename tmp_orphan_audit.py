import urllib.parse
import psycopg2
from app.db import DATABASE_URL

url = DATABASE_URL.replace('postgresql+asyncpg://', 'postgresql://', 1)
p = urllib.parse.urlparse(url)
conn = psycopg2.connect(host=p.hostname or 'localhost', port=p.port or 5432, dbname=p.path.lstrip('/'), user=p.username, password=p.password)
cur = conn.cursor()
cur.execute("SELECT m.fixture_id, m.league_id, m.season, m.match_time, m.status FROM matches m LEFT JOIN leagues l ON l.league_id = m.league_id WHERE l.league_id IS NULL ORDER BY m.fixture_id")
rows = cur.fetchall()
print('ORPHAN_COUNT', len(rows))
for row in rows:
    print(row)
cur.execute("SELECT tc.table_name, tc.constraint_name, tc.constraint_type, ccu.table_name AS foreign_table_name, ccu.column_name AS foreign_column_name FROM information_schema.table_constraints tc JOIN information_schema.key_column_usage kcu ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema JOIN information_schema.constraint_column_usage ccu ON ccu.constraint_name = tc.constraint_name AND ccu.table_schema = tc.table_schema WHERE tc.table_name = 'matches' AND tc.constraint_type = 'FOREIGN KEY'")
print('FK_INFO', cur.fetchall())
conn.close()
