import urllib.parse
import psycopg2
from app.db import DATABASE_URL

url = DATABASE_URL.replace('postgresql+asyncpg://', 'postgresql://', 1)
p = urllib.parse.urlparse(url)
conn = psycopg2.connect(host=p.hostname or 'localhost', port=p.port or 5432, dbname=p.path.lstrip('/'), user=p.username, password=p.password)
cur = conn.cursor()
cur.execute("SELECT conname, contype, pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid = 'matches'::regclass AND contype = 'f'")
print(cur.fetchall())
conn.close()
