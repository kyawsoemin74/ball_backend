import asyncio
import os
import traceback
from typing import Any

from sqlalchemy import text, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import async_session
from app.repositories.team_repository import TeamRepository
from app.services.base.football_client import FootballAPIClient
from app.services.cache_service import CacheService
from app.providers.team_provider import TeamProvider
from app.services.team_sync_service import TeamSyncService
from app.models.team import Team


async def snapshot(db: AsyncSession) -> dict:
    sql = text(
        """
        SELECT
            COUNT(*) AS total_teams,
            COUNT(provider_id) FILTER (WHERE provider_id IS NOT NULL) AS valid_provider_ids,
            COUNT(*) FILTER (WHERE provider = 'api-football' AND provider_id IS NULL) AS null_provider_ids,
            (SELECT COUNT(*) FROM (
                SELECT provider, provider_id
                FROM teams
                WHERE provider_id IS NOT NULL
                GROUP BY provider, provider_id
                HAVING COUNT(*) > 1
            ) dup) AS duplicate_provider_id_pairs,
            COALESCE((SELECT MAX(team_id) FROM teams), 0) AS max_team_id,
            COALESCE((SELECT last_value FROM teams_team_id_seq), 0) AS sequence_last_value,
            COALESCE((SELECT COUNT(*) FROM matches), 0) AS match_count,
            COALESCE((SELECT COUNT(*) FROM leagues), 0) AS league_count
        FROM teams
        """
    )
    result = await db.execute(sql)
    row = result.mappings().one()
    return {
        "TOTAL_TEAMS": int(row["total_teams"] or 0),
        "VALID_PROVIDER_IDS": int(row["valid_provider_ids"] or 0),
        "NULL_PROVIDER_IDS": int(row["null_provider_ids"] or 0),
        "DUPLICATE_PROVIDER_IDENTITIES": int(row["duplicate_provider_id_pairs"] or 0),
        "MAX_TEAM_ID": int(row["max_team_id"] or 0),
        "SEQUENCE_LAST_VALUE": int(row["sequence_last_value"] or 0),
        "MATCH_COUNT": int(row["match_count"] or 0),
        "LEAGUE_COUNT": int(row["league_count"] or 0),
    }


async def target_team_ids(db: AsyncSession) -> list[int]:
    rows = await db.execute(
        select(Team.team_id)
        .where(Team.provider == "api-football")
        .where(Team.provider_id.is_(None))
        .order_by(Team.team_id)
    )
    return [int(x[0]) for x in rows.all()]


async def execute() -> None:
    client = FootballAPIClient()
    team_provider = TeamProvider(client)
    repo = TeamRepository()
    sync = TeamSyncService(
        cache_service=CacheService(),
        team_repository=repo,
        team_provider=team_provider,
    )

    async with async_session() as db:
        before = await snapshot(db)
        print("BASELINE")
        for key, value in before.items():
            print(f"{key}={value}")

        # Discover all current API-Football teams whose provider_id is NULL.
        tasks = await target_team_ids(db)
        print("TARGET_COUNT", len(tasks))

        # Fully read-only evidence-backed dry-run of the resolver path first.
        # No mutation is permitted in this workspace's safe execution branch.
        for team_id in tasks:
            team = await repo.get_by_id(db, team_id)
            if team is None:
                continue

            provider_result = await team_provider.collect_team_evidence(
                team_name=getattr(team, "name", None),
                country=getattr(team, "country", None),
                league_id=getattr(team, "current_league_id", None),
                season=getattr(team, "current_season", None),
            )
            status = provider_result.get("provider_request_status", "NO_CANDIDATE")
            candidates = provider_result.get("candidates") or []
            print(
                f"DRY_RUN team_id={team.team_id} "
                f"name={team.name} provider_status={status} "
                f"candidate_count={len(candidates)}"
            )

        print("AFTER")
        after = await snapshot(db)
        for key, value in after.items():
            print(f"{key}={value}")


async def main() -> None:
    await execute()


if __name__ == "__main__":
    asyncio.run(main())
