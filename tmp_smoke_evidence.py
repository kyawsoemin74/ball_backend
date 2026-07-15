import json
import re
import urllib.request
import urllib.parse
import psycopg2
from app.db import DATABASE_URL
from app.core.config import settings

# Health
with urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=20) as resp:
    print('HEALTH_STATUS', resp.status)
    print('HEALTH_BODY', resp.read().decode())

# Metrics
with urllib.request.urlopen('http://127.0.0.1:8000/metrics', timeout=20) as resp:
    metrics_text = resp.read().decode()

for needle in ['fover_scheduler_up', 'fover_scheduler_job_runs_total', 'fover_scheduler_job_errors_total', 'fover_cache_hits_total', 'fover_cache_misses_total']:
    print('METRIC_PRESENT', needle, needle in metrics_text)

print('SYNC_DAILY_COUNTER_PRESENT', 'fover_scheduler_job_runs_total{job="sync_daily_fixtures"}' in metrics_text)

# DB integrity / session state
url = DATABASE_URL.replace('postgresql+asyncpg://', 'postgresql://', 1)
p = urllib.parse.urlparse(url)
conn = psycopg2.connect(host=p.hostname or 'localhost', port=p.port or 5432, dbname=p.path.lstrip('/'), user=p.username, password=p.password)
cur = conn.cursor()
cur.execute("SELECT COUNT(*) FROM matches WHERE fixture_id IS NULL")
print('NULL_FIXTURE_IDS', cur.fetchone()[0])
cur.execute("SELECT COUNT(*) FROM matches WHERE home_team_id IS NOT NULL AND away_team_id IS NOT NULL")
print('TEAM_IDS_PRESENT', cur.fetchone()[0])
cur.execute("SELECT COUNT(*) FROM matches m LEFT JOIN leagues l ON l.league_id = m.league_id WHERE m.league_id IS NOT NULL AND l.league_id IS NULL")
print('ORPHAN_LEAGUES', cur.fetchone()[0])
cur.execute("SELECT COUNT(*) FROM pg_stat_activity WHERE datname = %s AND state = 'idle in transaction'", (p.path.lstrip('/'),))
print('IDLE_TRANSACTIONS', cur.fetchone()[0])
cur.execute("SELECT COUNT(*) FROM pg_stat_activity WHERE datname = %s AND state IS NOT NULL AND state <> 'idle'", (p.path.lstrip('/'),))
print('ACTIVE_TRANSACTIONS', cur.fetchone()[0])
conn.close()
