import asyncio
import json
from unittest.mock import patch

from sqlalchemy import text

from app.db import async_session
from app.services.football import football_service
from app.services.scheduler import live_scheduler


async def stage_league(name: str, provider_id: str):
    async with async_session() as db:
        result = await db.execute(
            text(
                """
                INSERT INTO leagues (provider, provider_id, name, country, country_code, is_featured, display_order, created_at, updated_at)
                VALUES (:provider, :provider_id, :name, :country, :country_code, false, 999, NOW(), NOW())
                RETURNING league_id
                """
            ),
            {
                "provider": "api-football",
                "provider_id": provider_id,
                "name": name,
                "country": "Runtime Country",
                "country_code": "RT",
            },
        )
        league_id = int(result.scalar_one())
        await db.commit()
        return league_id


async def cleanup(league_id: int):
    async with async_session() as db:
        await db.execute(
            text("DELETE FROM league_identity_recovery WHERE league_id = :league_id"),
            {"league_id": league_id},
        )
        await db.execute(text("DELETE FROM leagues WHERE league_id = :league_id"), {"league_id": league_id})
        await db.commit()


async def insert_retry(league_id: int):
    async with async_session() as db:
        await db.execute(
            text(
                """
                INSERT INTO league_identity_recovery (
                    league_id, state, attempt_count, next_retry_at, last_attempted_at,
                    last_error_code, last_error_message, provider, resolved_provider_id,
                    resolved_at, created_at, updated_at
                ) VALUES (
                    :league_id, :state, 1, NOW() - INTERVAL '1 minute', NOW(),
                    :code, :message, :provider, NULL, NULL, NOW(), NOW()
                )
                ON CONFLICT (league_id) DO UPDATE
                SET state = EXCLUDED.state,
                    attempt_count = 1,
                    next_retry_at = NOW() - INTERVAL '1 minute',
                    updated_at = NOW()
                """
            ),
            {
                "league_id": league_id,
                "state": "IDENTITY_RESOLUTION_RETRY",
                "code": "NO_CANDIDATE",
                "message": "NO_CANDIDATE",
                "provider": "api-football",
            },
        )
        await db.commit()


async def scheduler_retry():
    league_id = await stage_league("RUNTIME_SCHEDULER_CHECK", "7777001")
    try:
        await insert_retry(league_id)
        svc = football_service.league_service.league_identity_recovery_service

        async def fake(name: str):
            return {
                "response": [
                    {
                        "league": {"id": 7777001, "name": name},
                        "country": {"name": "Runtime Country", "code": "RT"},
                    }
                ]
            }

        with patch.object(svc.league_provider, "get_leagues_by_name", side_effect=fake):
            metrics = await live_scheduler._recover_league_identities_job()
        return {"league_id": league_id, "metrics": metrics}
    finally:
        await cleanup(league_id)


async def same_league_lock():
    league_id = await stage_league("RUNTIME_LOCK_CHECK", "7777002")
    try:
        svc = football_service.league_service.league_identity_recovery_service

        async def fake(name: str):
            return {
                "response": [
                    {
                        "league": {"id": 7777002, "name": name},
                        "country": {"name": "Runtime Country", "code": "RT"},
                    }
                ]
            }

        with patch.object(svc.league_provider, "get_leagues_by_name", side_effect=fake):
            results = await asyncio.gather(
                football_service.league_service.recover_provider_identity(league_id),
                football_service.league_service.recover_provider_identity(league_id),
            )
        return {"league_id": league_id, "results": results}
    finally:
        await cleanup(league_id)


async def main():
    report = {
        "scheduler_retry": await scheduler_retry(),
        "same_league_lock": await same_league_lock(),
    }
    print(json.dumps(report, indent=2, default=str))


asyncio.run(main())
