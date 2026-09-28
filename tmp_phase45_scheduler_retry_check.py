import asyncio
import json
import uuid
from datetime import datetime, timezone
from unittest.mock import patch

from sqlalchemy import text

from app.db import async_session
from app.services.football import football_service
from app.services.scheduler import live_scheduler


async def create_temp_league():
    name = f"TMP_SCHEDULER_RETRY_{uuid.uuid4().hex[:8]}"
    provider_id = str(9000000 + int(uuid.uuid4().hex[:6], 16))
    async with async_session() as db:
        result = await db.execute(
            text(
                """
                INSERT INTO leagues (provider, provider_id, name, country, country_code, is_featured, display_order, created_at, updated_at)
                VALUES (:provider, NULL, :name, :country, :country_code, false, 999, NOW(), NOW())
                RETURNING league_id
                """
            ),
            {
                "provider": "api-football",
                "name": name,
                "country": "Runtime Country",
                "country_code": "RT",
            },
        )
        league_id = int(result.scalar_one())
        await db.commit()
    return league_id, name, provider_id


async def create_retry_record(league_id: int):
    async with async_session() as db:
        result = await db.execute(
            text(
                """
                INSERT INTO league_identity_recovery (
                    league_id, state, attempt_count, next_retry_at, last_attempted_at,
                    last_error_code, last_error_message, provider, resolved_provider_id,
                    resolved_at, created_at, updated_at
                ) VALUES (
                    :league_id, 'IDENTITY_RESOLUTION_RETRY', 1,
                    NOW() - INTERVAL '1 minute', NOW(),
                    'NO_CANDIDATE', 'NO_CANDIDATE', 'api-football', NULL,
                    NULL, NOW(), NOW()
                )
                ON CONFLICT (league_id) DO UPDATE
                SET state = EXCLUDED.state,
                    attempt_count = 1,
                    next_retry_at = NOW() - INTERVAL '1 minute',
                    updated_at = NOW()
                RETURNING recovery_id
                """
            ),
            {"league_id": league_id},
        )
        recovery_id = int(result.scalar_one())
        await db.commit()
    return recovery_id


async def read_retry_record(league_id: int):
    async with async_session() as db:
        result = await db.execute(
            text(
                """
                SELECT recovery_id, league_id, state, attempt_count, next_retry_at, last_error_code, last_error_message,
                       provider, resolved_provider_id, created_at, updated_at
                FROM league_identity_recovery
                WHERE league_id = :league_id
                """
            ),
            {"league_id": league_id},
        )
        row = result.mappings().first()
    return dict(row) if row else None


async def read_league_state(league_id: int):
    async with async_session() as db:
        result = await db.execute(
            text(
                """
                SELECT league_id, provider, provider_id, name, country, country_code
                FROM leagues
                WHERE league_id = :league_id
                """
            ),
            {"league_id": league_id},
        )
        row = result.mappings().first()
    return dict(row) if row else None


async def cleanup(league_id: int):
    async with async_session() as db:
        await db.execute(
            text("DELETE FROM league_identity_recovery WHERE league_id = :league_id"),
            {"league_id": league_id},
        )
        await db.execute(
            text("DELETE FROM leagues WHERE league_id = :league_id"),
            {"league_id": league_id},
        )
        await db.commit()


async def run_scheduler_retry_check():
    league_id, league_name, provider_id = await create_temp_league()
    try:
        recovery_id = await create_retry_record(league_id)
        before = await read_retry_record(league_id)
        scheduler_jobs = [
            {
                "id": job.id,
                "name": job.name,
                "trigger": str(job.trigger),
                "max_instances": job.max_instances,
            }
            for job in live_scheduler.scheduler.get_jobs()
            if job.id == "recover_league_identities"
        ]

        svc = football_service.league_service.league_identity_recovery_service

        async def fake_provider_lookup(name: str):
            return {
                "response": [
                    {
                        "league": {"id": int(provider_id), "name": name},
                        "country": {"name": "Runtime Country", "code": "RT"},
                    }
                ]
            }

        with patch.object(svc.league_provider, "get_leagues_by_name", side_effect=fake_provider_lookup):
            metrics = await live_scheduler._recover_league_identities_job()

        after_recovery = await read_retry_record(league_id)
        after_league = await read_league_state(league_id)
        duplicate_count = 0
        async with async_session() as db:
            duplicate_count = int(
                (
                    await db.execute(
                        text(
                            """
                            SELECT COUNT(*)
                            FROM league_identity_recovery
                            WHERE league_id = :league_id
                            """
                        ),
                        {"league_id": league_id},
                    )
                ).scalar_one()
            )
            provider_dupes = int(
                (
                    await db.execute(
                        text(
                            """
                            SELECT COUNT(*)
                            FROM leagues
                            WHERE provider = 'api-football' AND provider_id = :provider_id
                            """
                        ),
                        {"provider_id": provider_id},
                    )
                ).scalar_one()
            )

        return {
            "scheduler_jobs": scheduler_jobs,
            "league_name": league_name,
            "league_id": league_id,
            "recovery_id": recovery_id,
            "before_retry_record": before,
            "scheduler_metrics": metrics,
            "after_retry_record": after_recovery,
            "after_league_state": after_league,
            "duplicate_recovery_records": duplicate_count,
            "duplicate_provider_id_records": provider_dupes,
        }
    finally:
        await cleanup(league_id)


async def main():
    result = await run_scheduler_retry_check()
    print(json.dumps(result, indent=2, default=str))


asyncio.run(main())
