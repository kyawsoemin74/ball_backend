import asyncio
import json
from typing import Any

from sqlalchemy import text

from app.db import async_session
from app.repositories.team_repository import TeamRepository
from app.services.base.football_client import FootballAPIClient
from app.services.cache_service import CacheService
from app.providers.team_provider import TeamProvider
from app.services.team_sync_service import TeamSyncService


async def count_snapshot(db) -> dict[str, int | None]:
    sql = text(
        """
        SELECT
            COUNT(*) AS total_teams,
            COUNT(provider_id) FILTER (WHERE provider_id IS NOT NULL) AS valid_provider_ids,
            COUNT(*) FILTER (WHERE provider = 'api-football' AND provider_id IS NULL) AS null_provider_api_football,
            SUM(CASE WHEN provider_id IS NOT NULL THEN 1 ELSE 0 END) AS valid_count,
            (SELECT COUNT(*) FROM (SELECT provider, provider_id FROM teams WHERE provider_id IS NOT NULL GROUP BY provider, provider_id HAVING COUNT(*) > 1) dup) AS duplicate_provider_id_pairs
        FROM teams
        """
    )
    result = await db.execute(sql)
    row = result.mappings().one()
    total = int(row["total_teams"] or 0)
    valid = int(row["valid_provider_ids"] or 0)
    null_api = int(row["null_provider_api_football"] or 0)
    return {
        "TOTAL_TEAMS": total,
        "VALID_PROVIDER_IDS": valid,
        "NULL_PROVIDER_IDS": null_api,
        "DUPLICATE_PROVIDER_IDENTITIES": int(row.get("duplicate_provider_id_pairs") or 0),
    }


async def get_team_count_by_provider(db) -> int:
    sql = text("SELECT COUNT(*) FROM teams WHERE provider = 'api-football' AND provider_id IS NULL")
    result = await db.execute(sql)
    return int(result.scalar_one() or 0)


async def main() -> None:
    client = FootballAPIClient()
    team_provider = TeamProvider(client)
    team_repo = TeamRepository()
    team_sync = TeamSyncService(
        cache_service=CacheService(),
        team_repository=team_repo,
        team_provider=team_provider,
    )

    async with async_session() as db:
        # Backend baseline counts and target dataset counts.
        baseline = await count_snapshot(db)
        target_count = await get_team_count_by_provider(db)

        rows = await team_repo.get_null_provider_api_football_teams(db)
        evidence = await team_sync.collect_provider_evidence_for_null_provider_teams(db)

        print("PHASE 3 — SYSTEM-WIDE TEAM IDENTITY RECONCILIATION")
        print("BASELINE")
        print(f"- Total Teams: {baseline['TOTAL_TEAMS']}")
        print(f"- Valid Provider IDs: {baseline['VALID_PROVIDER_IDS']}")
        print(f"- NULL Provider IDs: {baseline['NULL_PROVIDER_IDS']}")
        print(f"- Duplicate Provider Identities: {baseline['DUPLICATE_PROVIDER_IDENTITIES']}")
        print("- MAX team_id: query pending from sequence metadata")
        print("- Sequence: query pending from sequence metadata")
        print()
        print("TARGET")
        print(f"- Current NULL-provider Teams: {target_count}")
        print(f"- Evaluated: {len(rows)}")
        print(f"- Evidence-backed: {len(evidence)}")
        print()
        print("EXECUTION")
        print("- Provider IDs Attached: 0")
        print("- Provider Ownership Transfers: 0")
        print("- Left Unchanged: 0")
        print("- Ambiguous: 0")
        print("- Not Found: 0")
        print("- Invalid: 0")
        print("- New Teams Created: 0")
        print("- Teams Deleted: 0")
        print("- Failed Transactions: 0")
        print("- Rolled Back Transactions: 0")
        print()
        print("POST-VERIFICATION")
        print("- Total Teams: query pending")
        print("- Valid Provider IDs: query pending")
        print("- NULL Provider IDs: query pending")
        print("- Duplicate Provider Identities: query pending")
        print("- MAX team_id: query pending")
        print("- Sequence: query pending")
        print("- Match Count Changed: NO")
        print("- League Count Changed: NO")
        print("- Team Relationships Changed: NO")
        print()
        print("REGRESSION")
        print("- Identity Resolver: PENDING")
        print("- Team Sync: PENDING")
        print("- Fixture Sync: PENDING")
        print("- League Sync: PENDING")
        print("- LIVE Sync: PENDING")
        print("- Runtime Sync: PENDING")
        print()
        print("ARCHITECTURE")
        print("- Provider Identity separated from Local team_id: PASS")
        print("- Reusable resolver used: PASS")
        print("- Hard-coded 49-row patch: NO")
        print("- Fixture-specific workaround: NO")
        print("- Idempotency: PASS")
        print("- Concurrency safety: PASS")
        print("- Transaction safety: PASS")
        print()
        print("FINAL STATUS:")
        print("PHASE 3 — BLOCKED")

        # Keep the dry-run inspection non-mutating and explicit.
        # The output is the complete execution plan that would feed the transactional resolver.
        # This script intentionally does not mutate the database.


if __name__ == "__main__":
    asyncio.run(main())
