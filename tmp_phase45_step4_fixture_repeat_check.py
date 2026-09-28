import asyncio
import json
from unittest.mock import patch

from sqlalchemy import text

from app.db import async_session
from app.services.football import football_service

FIXTURE_ID = 1234567
LEAGUE_PROVIDER_ID = "9901001"
TEAM_HOME_ID = 9902001
TEAM_AWAY_ID = 9902002
LEAGUE_NAME = "RUNTIME_FIXTURE_REPEAT_CHECK"


async def cleanup(league_id: int):
    async with async_session() as db:
        await db.execute(
            text("DELETE FROM matches WHERE league_id = :league_id"),
            {"league_id": league_id},
        )
        await db.execute(
            text("DELETE FROM league_seasons WHERE league_id = :league_id"),
            {"league_id": league_id},
        )
        await db.execute(
            text("DELETE FROM teams WHERE provider_id IN (:home_id, :away_id)"),
            {"home_id": str(TEAM_HOME_ID), "away_id": str(TEAM_AWAY_ID)},
        )
        await db.execute(
            text("DELETE FROM allowed_leagues WHERE league_id = :league_id"),
            {"league_id": league_id},
        )
        await db.execute(
            text("DELETE FROM leagues WHERE league_id = :league_id"),
            {"league_id": league_id},
        )
        await db.commit()


async def create_temp_league() -> int:
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
                "provider_id": LEAGUE_PROVIDER_ID,
                "name": LEAGUE_NAME,
                "country": "Runtime Country",
                "country_code": "RT",
            },
        )
        league_id = int(result.scalar_one())
        await db.execute(
            text("INSERT INTO allowed_leagues (league_id) VALUES (:league_id) ON CONFLICT DO NOTHING"),
            {"league_id": league_id},
        )
        await db.commit()
        return league_id


async def get_match_count(db, league_id: int) -> int:
    row = await db.execute(
        text(
            "SELECT COUNT(*) FROM matches WHERE league_id = :league_id AND provider = 'api-football' AND provider_fixture_id = :fixture_id"
        ),
        {"league_id": league_id, "fixture_id": FIXTURE_ID},
    )
    return int(row.scalar_one())


async def get_team_snapshot(db) -> dict:
    result = await db.execute(
        text(
            "SELECT team_id, provider, provider_id, name FROM teams "
            "WHERE provider_id IN (:home_id, :away_id) ORDER BY provider_id"
        ),
        {"home_id": str(TEAM_HOME_ID), "away_id": str(TEAM_AWAY_ID)},
    )
    rows = [dict(row) for row in result.mappings().all()]
    return {
        "count": len(rows),
        "rows": rows,
        "provider_ids": [row["provider_id"] for row in rows],
        "team_ids": [row["team_id"] for row in rows],
    }


async def run_check():
    league_id = await create_temp_league()
    fixture = {
        "fixture": {
            "id": FIXTURE_ID,
            "date": "2026-10-20T18:00:00+00:00",
            "status": {"short": "NS", "elapsed": 0},
            "venue": {"name": "Runtime Venue", "city": "Runtime City"},
            "referee": None,
        },
        "league": {
            "id": int(LEAGUE_PROVIDER_ID),
            "name": LEAGUE_NAME,
            "country": "Runtime Country",
            "season": 2026,
            "flag": "",
            "logo": "",
        },
        "teams": {
            "home": {"id": TEAM_HOME_ID, "name": "Runtime Home Team", "logo": ""},
            "away": {"id": TEAM_AWAY_ID, "name": "Runtime Away Team", "logo": ""},
        },
        "goals": {"home": 0, "away": 0},
        "score": {
            "halftime": {"home": 0, "away": 0},
            "fulltime": {"home": 0, "away": 0},
            "extratime": {"home": 0, "away": 0},
            "penalty": {"home": 0, "away": 0},
        },
    }

    async def fake_get_fixtures(league: str | int, season: int):
        return {"response": [fixture]}

    async def fake_get_team_details(team_id: int):
        teams = {
            TEAM_HOME_ID: {"name": "Runtime Home Team", "logo": ""},
            TEAM_AWAY_ID: {"name": "Runtime Away Team", "logo": ""},
        }
        team = teams.get(int(team_id))
        if team is None:
            return {"response": []}
        return {
            "response": [
                {
                    "team": {
                        "id": int(team_id),
                        "name": team["name"],
                        "country": "Runtime Country",
                        "logo": team["logo"],
                    },
                    "venue": {"name": "Runtime Venue", "city": "Runtime City"},
                }
            ]
        }

    async def fake_sync_team_coach(db, team_id: int):
        return {"success": True, "team_id": team_id, "coach_id": None, "updated": False}

    try:
        with (
            patch.object(football_service.fixture_sync_service.fixture_provider, "get_fixtures", side_effect=fake_get_fixtures),
            patch.object(football_service.team_service.team_provider, "get_team_details", side_effect=fake_get_team_details),
            patch.object(football_service.team_sync_service, "sync_team_coach", side_effect=fake_sync_team_coach),
        ):
            async with async_session() as db:
                first = await football_service.sync_full_season(db, league_id, 2026)
                await db.commit()
                first_count = await get_match_count(db, league_id)
                first_teams = await get_team_snapshot(db)

                second = await football_service.sync_full_season(db, league_id, 2026)
                await db.commit()
                second_count = await get_match_count(db, league_id)
                second_teams = await get_team_snapshot(db)

                total_rows = await db.execute(
                    text("SELECT COUNT(*) FROM matches WHERE league_id = :league_id"),
                    {"league_id": league_id},
                )
                total_matches = int(total_rows.scalar_one())

        return {
            "league_id": league_id,
            "first_sync": first,
            "second_sync": second,
            "first_match_count": first_count,
            "second_match_count": second_count,
            "first_team_snapshot": first_teams,
            "second_team_snapshot": second_teams,
            "total_matches_for_league": total_matches,
            "fixture_id": FIXTURE_ID,
        }
    finally:
        await cleanup(league_id)


async def main():
    output = await run_check()
    print(json.dumps(output, indent=2, default=str, sort_keys=True))


asyncio.run(main())
