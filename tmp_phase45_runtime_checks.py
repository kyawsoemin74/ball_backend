import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from sqlalchemy import text

os.environ['DATABASE_URL'] = 'postgresql://user:password@postgres:5432/fover_db'
os.environ['REDIS_URL'] = 'redis://redis:6379/0'

from app.db import async_session
from app.services.football import football_service
from app.services.scheduler import live_scheduler


async def ensure_league(name: str, provider: str = 'api-football', provider_id: str | None = None):
    async with async_session() as db:
        row = await db.execute(
            text(
                """
                INSERT INTO leagues (provider, provider_id, name, country, country_code, is_featured, display_order, created_at, updated_at)
                VALUES (:provider, :provider_id, :name, :country, :country_code, false, 999, NOW(), NOW())
                RETURNING league_id
                """
            ),
            {
                'provider': provider,
                'provider_id': provider_id,
                'name': name,
                'country': 'Runtime Country',
                'country_code': 'RT',
            },
        )
        league_id = int(row.scalar_one())
        await db.commit()
        return league_id


async def cleanup_league(league_id: int, team_ids: list[int] | None = None):
    async with async_session() as db:
        if team_ids:
            await db.execute(
                text('DELETE FROM teams WHERE provider_id = ANY(:team_ids)'),
                {'team_ids': [str(tid) for tid in team_ids]},
            )
        await db.execute(text('DELETE FROM matches WHERE league_id = :league_id'), {'league_id': league_id})
        await db.execute(text('DELETE FROM allowed_leagues WHERE league_id = :league_id'), {'league_id': league_id})
        await db.execute(text('DELETE FROM league_identity_recovery WHERE league_id = :league_id'), {'league_id': league_id})
        await db.execute(text('DELETE FROM leagues WHERE league_id = :league_id'), {'league_id': league_id})
        await db.commit()


async def insert_retry_record(league_id: int, *, state: str = 'IDENTITY_RESOLUTION_RETRY', attempt_count: int = 1, next_retry_at: datetime | None = None):
    if next_retry_at is None:
        next_retry_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    async with async_session() as db:
        await db.execute(
            text(
                """
                INSERT INTO league_identity_recovery (league_id, state, attempt_count, next_retry_at, last_attempted_at, last_error_code, last_error_message, provider, resolved_provider_id, resolved_at, created_at, updated_at)
                VALUES (:league_id, :state, :attempt_count, :next_retry_at, NOW(), 'NO_CANDIDATE', 'NO_CANDIDATE', 'api-football', NULL, NULL, NOW(), NOW())
                ON CONFLICT (league_id) DO UPDATE SET
                    state = EXCLUDED.state,
                    attempt_count = EXCLUDED.attempt_count,
                    next_retry_at = EXCLUDED.next_retry_at,
                    last_attempted_at = NOW(),
                    last_error_code = EXCLUDED.last_error_code,
                    last_error_message = EXCLUDED.last_error_message,
                    updated_at = NOW()
                """
            ),
            {
                'league_id': league_id,
                'state': state,
                'attempt_count': attempt_count,
                'next_retry_at': next_retry_at,
            },
        )
        await db.commit()


async def read_retry_state(league_id: int):
    async with async_session() as db:
        row = await db.execute(
            text(
                'SELECT state, attempt_count, last_error_code, next_retry_at, resolved_provider_id FROM league_identity_recovery WHERE league_id = :league_id'
            ),
            {'league_id': league_id},
        )
        return row.fetchone()


async def run_scheduler_retry_check():
    league_id = await ensure_league(f'RUNTIME_SCHEDULER_RETRY_{datetime.now(timezone.utc).strftime("%H%M%S")}', provider='api-football', provider_id='7777001')
    try:
        await insert_retry_record(league_id)
        service = football_service.league_service.league_identity_recovery_service

        async def fake_get_leagues_by_name(name: str):
            await asyncio.sleep(0.2)
            return {'response': [{'league': {'id': 7777001, 'name': name}, 'country': {'name': 'Runtime Country', 'code': 'RT'}}]}

        with patch.object(service.league_provider, 'get_leagues_by_name', side_effect=fake_get_leagues_by_name):
            metrics = await live_scheduler._recover_league_identities_job()
        state = await read_retry_state(league_id)
        return {'league_id': league_id, 'metrics': metrics, 'state': state}
    finally:
        await cleanup_league(league_id)


async def run_worker_restart_check():
    league_id = await ensure_league(f'RUNTIME_RESTART_{datetime.now(timezone.utc).strftime("%H%M%S")}', provider='api-football', provider_id='7777002')
    try:
        await insert_retry_record(league_id, next_retry_at=datetime.now(timezone.utc) - timedelta(minutes=1))
        service = football_service.league_service.league_identity_recovery_service

        async def fake_get_leagues_by_name(name: str):
            await asyncio.sleep(0.2)
            return {'response': [{'league': {'id': 7777002, 'name': name}, 'country': {'name': 'Runtime Country', 'code': 'RT'}}]}

        live_scheduler.stop()
        live_scheduler.start()
        with patch.object(service.league_provider, 'get_leagues_by_name', side_effect=fake_get_leagues_by_name):
            metrics = await live_scheduler._recover_league_identities_job()
        state = await read_retry_state(league_id)
        return {'league_id': league_id, 'metrics': metrics, 'state': state}
    finally:
        live_scheduler.stop()
        await cleanup_league(league_id)


async def run_lock_contention_check():
    league_id = await ensure_league(f'RUNTIME_LOCK_{datetime.now(timezone.utc).strftime("%H%M%S")}', provider='api-football', provider_id='7777003')
    try:
        service = football_service.league_service.league_identity_recovery_service

        async def fake_get_leagues_by_name(name: str):
            await asyncio.sleep(1.5)
            return {'response': [{'league': {'id': 7777003, 'name': name}, 'country': {'name': 'Runtime Country', 'code': 'RT'}}]}

        with patch.object(service.league_provider, 'get_leagues_by_name', side_effect=fake_get_leagues_by_name):
            r1, r2 = await asyncio.gather(
                football_service.league_service.recover_provider_identity(league_id),
                football_service.league_service.recover_provider_identity(league_id),
            )
        return {'league_id': league_id, 'results': [r1, r2]}
    finally:
        await cleanup_league(league_id)


async def run_parallel_different_league_check():
    league_a = await ensure_league(f'RUNTIME_PARALLEL_A_{datetime.now(timezone.utc).strftime("%H%M%S")}', provider='api-football', provider_id='7777004')
    league_b = await ensure_league(f'RUNTIME_PARALLEL_B_{datetime.now(timezone.utc).strftime("%H%M%S")}', provider='api-football', provider_id='7777005')
    try:
        service = football_service.league_service.league_identity_recovery_service

        async def fake_get_leagues_by_name(name: str):
            await asyncio.sleep(0.5)
            if 'A_' in name:
                return {'response': [{'league': {'id': 7777004, 'name': name}, 'country': {'name': 'Runtime Country', 'code': 'RT'}}]}
            return {'response': [{'league': {'id': 7777005, 'name': name}, 'country': {'name': 'Runtime Country', 'code': 'RT'}}]}

        with patch.object(service.league_provider, 'get_leagues_by_name', side_effect=fake_get_leagues_by_name):
            results = await asyncio.gather(
                football_service.league_service.recover_provider_identity(league_a),
                football_service.league_service.recover_provider_identity(league_b),
            )
        return {'league_ids': [league_a, league_b], 'results': results}
    finally:
        await cleanup_league(league_a)
        await cleanup_league(league_b)


async def run_fixture_idempotency_check():
    league_id = await ensure_league(f'RUNTIME_FIXTURE_IDEMPOTENT_{datetime.now(timezone.utc).strftime("%H%M%S")}', provider='api-football', provider_id='9900001')
    try:
        async with async_session() as db:
            await db.execute(text('INSERT INTO allowed_leagues (league_id) VALUES (:league_id) ON CONFLICT DO NOTHING'), {'league_id': league_id})
            await db.commit()

        fixture = {
            'fixture': {'id': 9900000001, 'date': '2026-10-20T18:00:00+00:00', 'status': {'short': 'NS', 'elapsed': 0}, 'venue': {'name': 'Runtime Venue', 'city': 'Runtime City'}},
            'league': {'id': 9900001, 'name': f'RUNTIME_FIXTURE_IDEMPOTENT_{datetime.now(timezone.utc).strftime("%H%M%S")}', 'country': 'Runtime Country', 'season': 2026, 'flag': '', 'logo': ''},
            'teams': {'home': {'id': 9910001, 'name': 'Runtime Home Team', 'logo': ''}, 'away': {'id': 9910002, 'name': 'Runtime Away Team', 'logo': ''}},
            'goals': {'home': 0, 'away': 0},
        }

        async with async_session() as db:
            first = await football_service.fixture_sync_service.process_fixture(db, fixture)
            await db.commit()
            second = await football_service.fixture_sync_service.process_fixture(db, fixture)
            await db.commit()

        async with async_session() as db:
            count = int(
                (
                    await db.execute(
                        text('SELECT COUNT(*) FROM matches WHERE provider = :provider AND provider_fixture_id = :provider_fixture_id'),
                        {'provider': 'api-football', 'provider_fixture_id': 9900000001},
                    )
                ).scalar_one()
            )
        return {'league_id': league_id, 'first': first, 'second': second, 'match_count': count}
    finally:
        await cleanup_league(league_id, team_ids=[9910001, 9910002])


async def main():
    results = {
        'scheduler_retry': await run_scheduler_retry_check(),
        'worker_restart_persistence': await run_worker_restart_check(),
        'lock_contention': await run_lock_contention_check(),
        'parallel_different_leagues': await run_parallel_different_league_check(),
        'fixture_idempotency': await run_fixture_idempotency_check(),
    }
    print(json.dumps(results, default=str, indent=2, sort_keys=True))


asyncio.run(main())
