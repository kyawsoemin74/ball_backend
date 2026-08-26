import asyncio
from pathlib import Path
from sqlalchemy import text
from app.db import async_session
from app.services.event_service import EventService
from app.services.base.football_client import FootballAPIClient
from app.services.cache_service import CacheService

MATCH_IDS = [1492315, 1492316, 1492317, 1492319]
BACKUP_PATHS = [
    Path('backup_fover_db_20260817_000552.sql'),
    Path('backups/fover_db_backup_20260817_162533.sql'),
]

async def fetch_scalar(db, sql, params=None):
    res = await db.execute(text(sql), params or {})
    return res.scalar_one()

async def fetch_all(db, sql, params=None):
    res = await db.execute(text(sql), params or {})
    return res.fetchall()

async def main():
    print('=== BACKUP GATE ===')
    for path in BACKUP_PATHS:
        exists = path.exists()
        size = path.stat().st_size if exists else 0
        print(f'{path.as_posix()} exists={exists} size={size}')
        if not exists or size <= 0:
            raise RuntimeError('BACKUP_GATE_FAILED')

    async with async_session() as db:
        print('=== DB IDENTITY ===')
        print(await fetch_all(db, 'SELECT current_database(), current_user, current_schema(), version();'))
        print(await fetch_all(db, 'SELECT version_num FROM alembic_version;'))

        print('=== BEFORE BASELINE ===')
        players_count_before = await fetch_scalar(db, 'SELECT COUNT(*) FROM players')
        event_count_before = await fetch_scalar(db, 'SELECT COUNT(*) FROM match_events')
        orphan_count_before = await fetch_scalar(db, "SELECT COUNT(*) FROM match_events me LEFT JOIN players p ON p.player_id = me.player_id WHERE me.player_id IS NOT NULL AND p.player_id IS NULL")
        print('players_count_before=', players_count_before)
        print('event_count_before=', event_count_before)
        print('orphan_count_before=', orphan_count_before)

        print('=== MATCH_SCOPE_CHECK ===')
        affected_matches = await fetch_all(db, 'SELECT DISTINCT match_id FROM match_events WHERE match_id IN (:m1,:m2,:m3,:m4) ORDER BY match_id', {'m1':1492315,'m2':1492316,'m3':1492317,'m4':1492319})
        print('affected_matches=', affected_matches)
        if [r[0] for r in affected_matches] != MATCH_IDS:
            raise RuntimeError(f'SCOPE_VIOLATION:{affected_matches}')

        service = EventService(client=FootballAPIClient(), cache_service=CacheService())

        for match_id in MATCH_IDS:
            print(f'=== START MATCH {match_id} ===')
            before_count = await fetch_scalar(db, 'SELECT COUNT(*) FROM match_events WHERE match_id = :match_id', {'match_id': match_id})
            before_bad = await fetch_scalar(db, "SELECT COUNT(*) FROM match_events me LEFT JOIN players p ON p.player_id = me.player_id WHERE me.match_id = :match_id AND me.player_id IS NOT NULL AND p.player_id IS NULL", {'match_id': match_id})
            print('before_count=', before_count)
            print('before_bad=', before_bad)

            result = await service.sync_match_events(db, match_id)
            print('sync_result=', {'success': result.get('success'), 'match_id': match_id, 'count': result.get('count'), 'api_events': len(result.get('api_events', []))})
            if not result.get('success'):
                raise RuntimeError(f'MATCH_SYNC_FAILED:{match_id}')

            await db.commit()

            after_count = await fetch_scalar(db, 'SELECT COUNT(*) FROM match_events WHERE match_id = :match_id', {'match_id': match_id})
            after_bad = await fetch_scalar(db, "SELECT COUNT(*) FROM match_events me LEFT JOIN players p ON p.player_id = me.player_id WHERE me.match_id = :match_id AND me.player_id IS NOT NULL AND p.player_id IS NULL", {'match_id': match_id})
            print('after_count=', after_count)
            print('after_bad=', after_bad)

            bad_rows = await fetch_all(db, "SELECT me.id, me.match_id, me.player_id, p.provider_id AS provider_id, p.player_id AS master_player_id FROM match_events me LEFT JOIN players p ON p.player_id = me.player_id WHERE me.match_id = :match_id AND me.player_id IS NOT NULL AND p.player_id IS NULL ORDER BY me.id", {'match_id': match_id})
            print('bad_rows=', bad_rows)
            if bad_rows:
                raise RuntimeError(f'EVENT_PLAYER_ORPHANS_REMAIN:{match_id}:{bad_rows}')

            assist_rows = await fetch_all(db, "SELECT me.id, me.assist_id FROM match_events me WHERE me.match_id = :match_id AND me.assist_id IS NOT NULL ORDER BY me.id LIMIT 5", {'match_id': match_id})
            print('assist_sample=', assist_rows)

        print('=== FINAL POSTCHECK ===')
        players_count_after = await fetch_scalar(db, 'SELECT COUNT(*) FROM players')
        event_count_after = await fetch_scalar(db, 'SELECT COUNT(*) FROM match_events')
        orphan_count_after = await fetch_scalar(db, "SELECT COUNT(*) FROM match_events me LEFT JOIN players p ON p.player_id = me.player_id WHERE me.player_id IS NOT NULL AND p.player_id IS NULL")
        print('players_count_after=', players_count_after)
        print('event_count_after=', event_count_after)
        print('orphan_count_after=', orphan_count_after)
        print('teams_count=', await fetch_scalar(db, 'SELECT COUNT(*) FROM teams'))
        print('matches_count=', await fetch_scalar(db, 'SELECT COUNT(*) FROM matches'))
        print('standings_count=', await fetch_scalar(db, 'SELECT COUNT(*) FROM standings'))
        print('final_reconciliation_check=', await fetch_all(db, "SELECT COUNT(*) FROM match_events me JOIN players p ON p.provider = 'api-football' AND p.provider_id = CAST(me.player_id AS text) WHERE me.match_id IN (:m1,:m2,:m3,:m4)", {'m1':1492315,'m2':1492316,'m3':1492317,'m4':1492319}))
        if orphan_count_after != 0:
            raise RuntimeError(f'EVENT_PLAYER_ORPHANS_REMAIN:{orphan_count_after}')

asyncio.run(main())
