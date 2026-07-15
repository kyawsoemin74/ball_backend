import urllib.parse
import psycopg2
from app.db import DATABASE_URL

url = DATABASE_URL.replace('postgresql+asyncpg://', 'postgresql://', 1)
p = urllib.parse.urlparse(url)
conn = psycopg2.connect(host=p.hostname or 'localhost', port=p.port or 5432, dbname=p.path.lstrip('/'), user=p.username, password=p.password)
cur = conn.cursor()
cur.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename IN ('matches','leagues')")
print('TABLES', cur.fetchall())
cur.execute("SELECT * FROM information_schema.columns WHERE table_name='matches' AND column_name='league_id'")
print('MATCH_COLUMNS', cur.fetchall())
cur.execute("SELECT * FROM information_schema.columns WHERE table_name='leagues' AND column_name='league_id'")
print('LEAGUE_COLUMNS', cur.fetchall())
conn.close()
