import asyncio
from sqlalchemy import text
from app.db import async_session


async def main():
    async with async_session() as db:
        # Core totals and counts
        base_sql = text(
            """
            SELECT
                COUNT(*) AS total_teams,
                COUNT(provider_id) FILTER (WHERE provider_id IS NOT NULL) AS valid_provider_ids,
                COUNT(*) FILTER (WHERE provider = 'api-football' AND provider_id IS NOT NULL) AS api_valid_provider_ids,
                COUNT(*) FILTER (WHERE provider = 'api-football' AND provider_id IS NULL) AS api_null_provider_ids,
                COUNT(*) FILTER (WHERE provider = 'api-football' AND provider_id = '') AS api_empty_provider_ids,
                COALESCE((SELECT COUNT(*) FROM (
                    SELECT provider, provider_id
                    FROM teams
                    WHERE provider_id IS NOT NULL
                    GROUP BY provider, provider_id
                    HAVING COUNT(*) > 1
                ) x), 0) AS duplicate_provider_identity_pairs,
                COALESCE((SELECT MAX(team_id) FROM teams), 0) AS max_team_id
            FROM teams
            """
        )
        base = await db.execute(base_sql)
        row = base.mappings().one()

        # Current null rows
        null_sql = text(
            """
            SELECT
                team_id,
                provider,
                provider_id,
                name,
                country,
                current_league_id,
                current_season,
                created_at,
                updated_at
            FROM teams
            WHERE provider = 'api-football'
              AND provider_id IS NULL
            ORDER BY team_id
            """
        )
        null_rows_result = await db.execute(null_sql)
        null_rows = null_rows_result.mappings().all()

        # Duplicate provider identity rows
        dup_sql = text(
            """
            SELECT
                provider,
                provider_id,
                COUNT(*) AS count
            FROM teams
            WHERE provider_id IS NOT NULL
            GROUP BY provider, provider_id
            HAVING COUNT(*) > 1
            """
        )
        dup_result = await db.execute(dup_sql)
        dup_rows = dup_result.mappings().all()

        # PK duplicate check
        pkdup_sql = text(
            """
            SELECT team_id, COUNT(*) AS count
            FROM teams
            GROUP BY team_id
            HAVING COUNT(*) > 1
            """
        )
        pkdup_result = await db.execute(pkdup_sql)
        pkdups = pkdup_result.mappings().all()

        # Relationship counts
        match_sql = text("SELECT COUNT(*) AS count FROM matches")
        match_result = await db.execute(match_sql)
        match_count = int(match_result.scalar_one() or 0)
        league_sql = text("SELECT COUNT(*) AS count FROM leagues")
        league_result = await db.execute(league_sql)
        league_count = int(league_result.scalar_one() or 0)

        # Derived
        current_null_count = len(null_rows)
        resolved = 49 - current_null_count if current_null_count <= 49 else 0
        remaining = current_null_count
        total = int(row["total_teams"])
        valid = int(row["valid_provider_ids"])
        dup_count = int(row["duplicate_provider_identity_pairs"])
        invalid = int(row["api_empty_provider_ids"])
        max_team_id = int(row["max_team_id"])

        # Report classification
        # PASS if current API-Football provider_id NULL count = 0 and no dup and no invalid provider IDs
        # PARTIAL if some previous 49 were resolved but some remain.
        # FAIL if inconsistent or new corruption.
        if current_null_count == 0 and dup_count == 0 and invalid == 0:
            status = "PASS"
        elif current_null_count < 49 and current_null_count > 0 and dup_count == 0 and invalid == 0:
            status = "PARTIAL"
        else:
            status = "FAIL"

        # Print report in required format
        print("TEAM MASTER — NULL PROVIDER_ID FINAL VERIFICATION")
        print("Previous NULL provider_id : 49")
        print(f"Current NULL provider_id  : {current_null_count}")
        print(f"Resolved                  : {resolved}")
        print(f"Remaining                 : {remaining}")
        print()
        print(f"Total Teams               : {total}")
        print(f"Valid provider_id         : {valid}")
        print(f"Duplicate identities      : {dup_count}")
        print(f"Invalid provider_id       : {invalid}")
        print(f"MAX(team_id)              : {max_team_id}")
        print()
        print(f"New NULL rows detected    : {'YES' if current_null_count == 0 else 'NO'}")
        print()
        print("Database mutation         : NONE")
        print()
        print(f"FINAL STATUS              : {status}")
        print()
        print("NULL ROW DETAILS")
        for r in null_rows:
            print(r)
        print()
        print("DUPLICATE PROVIDER IDENTITIES DETAILS")
        for r in dup_rows:
            print(r)
        print()
        print("PK DUPLICATES DETAILS")
        for r in pkdups:
            print(r)
        print()
        print("COUNTS")
        print(f"TOTAL_TEAMS={total}")
        print(f"VALID_PROVIDER_IDS={valid}")
        print(f"NULL_PROVIDER_IDS={current_null_count}")
        print(f"DUPLICATE_PROVIDER_IDENTITIES={dup_count}")
        print(f"MAX_TEAM_ID={max_team_id}")
        print(f"MATCH_COUNT={match_count}")
        print(f"LEAGUE_COUNT={league_count}")


if __name__ == "__main__":
    asyncio.run(main())
