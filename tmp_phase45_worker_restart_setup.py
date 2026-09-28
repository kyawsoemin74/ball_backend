import asyncio
import json
import sys
import uuid

sys.path.insert(0, "/usr/src/app")

from sqlalchemy import text

from app.db import async_session


async def main():
    name = f"TMP_RESTART_PERSIST_{uuid.uuid4().hex[:8]}"
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
        await db.execute(
            text(
                """
                INSERT INTO league_identity_recovery (
                    league_id, state, attempt_count, next_retry_at, last_attempted_at,
                    last_error_code, last_error_message, provider, resolved_provider_id,
                    resolved_at, created_at, updated_at
                ) VALUES (
                    :league_id, 'IDENTITY_RESOLUTION_RETRY', 1, NOW() - INTERVAL '1 minute', NOW(),
                    'NO_CANDIDATE', 'NO_CANDIDATE', 'api-football', NULL, NULL, NOW(), NOW()
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
        await db.commit()
        print(json.dumps({"league_id": league_id, "name": name}, indent=2))


asyncio.run(main())
