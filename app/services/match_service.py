import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache import make_cache_key
from app.models.match import Match
from app.repositories.match_repository import MatchRepository
from app.schemas.match import MatchCreate
from app.services.base.football_client import FootballAPIClient
from app.services.cache_service import CacheService
from app.services.team_service import TeamService

logger = logging.getLogger(__name__)

FINISHED_STATUSES = {"FT", "AET", "PEN", "CANC", "ABD", "AWD", "WO"}
LIVE_STATUSES = {"1H", "2H", "HT", "ET", "LIVE", "BT", "P"}


class MatchService:
    def __init__(self, client: FootballAPIClient, team_service: TeamService, cache_service: CacheService | None = None) -> None:
        self.client = client
        self.team_service = team_service
        self.cache_service = cache_service or CacheService()
        self.match_repository = MatchRepository()

    def _extract_id_from_logo(self, obj: dict) -> Optional[int]:
        url = obj.get("logo")
        if not url or not isinstance(url, str):
            return None
        match = re.search(r"/teams/(\d+)", url)
        if match:
            try:
                return int(match.group(1))
            except (ValueError, TypeError):
                return None
        return None

    def parse_fixture_to_match(self, fixture: dict) -> Optional[MatchCreate]:
        try:
            f_info = fixture.get("fixture", {})
            f_league = fixture.get("league", {})
            f_teams = fixture.get("teams", {})
            f_goals = fixture.get("goals", {})
            date_str = f_info.get("date")
            if not date_str:
                return None

            dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            home_team_id = f_teams.get("home", {}).get("id") or self._extract_id_from_logo(f_teams.get("home", {}))
            away_team_id = f_teams.get("away", {}).get("id") or self._extract_id_from_logo(f_teams.get("away", {}))
            return MatchCreate(
                match_id=int(f_info.get("id")),
                league_id=f_league.get("id"),
                league_name=f_league.get("name"),
                league_logo=f_league.get("logo"),
                country_name=f_league.get("country"),
                country_logo=f_league.get("flag"),
                match_time=dt,
                status=f_info.get("status", {}).get("short", "NS"),
                elapsed=f_info.get("status", {}).get("elapsed", 0) or 0,
                home_team=f_teams.get("home", {}).get("name"),
                home_team_id=home_team_id,
                home_team_logo=f_teams.get("home", {}).get("logo"),
                away_team=f_teams.get("away", {}).get("name"),
                away_team_id=away_team_id,
                away_team_logo=f_teams.get("away", {}).get("logo"),
                home_score=f_goals.get("home") or 0,
                away_score=f_goals.get("away") or 0,
                venue_name=f_info.get("venue", {}).get("name", "Unknown"),
                venue_city=f_info.get("venue", {}).get("city", "Unknown"),
            )
        except Exception as exc:
            logger.error("Parsing error for Fixture ID %s: %s", fixture.get("fixture", {}).get("id"), exc)
            return None

    async def _process_sync(self, db: AsyncSession, fixtures: list) -> dict:
        parsed_matches = []
        for fixture_raw in fixtures:
            match = self.parse_fixture_to_match(fixture_raw)
            if match:
                parsed_matches.append(match)
            else:
                logger.warning("Fixture ID %s failed parsing.", fixture_raw.get("fixture", {}).get("id"))

        if not parsed_matches:
            return {"success": True, "inserted": 0, "updated": 0, "total": 0}

        needed_teams = {}
        for match in parsed_matches:
            if match.home_team_id and match.home_team_id not in needed_teams:
                needed_teams[match.home_team_id] = {"name": match.home_team, "logo": match.home_team_logo}
            if match.away_team_id and match.away_team_id not in needed_teams:
                needed_teams[match.away_team_id] = {"name": match.away_team, "logo": match.away_team_logo}

        if needed_teams:
            await self.team_service.ensure_teams_exist(db, [{"team_id": tid, "name": info["name"], "logo": info["logo"]} for tid, info in needed_teams.items()])

        match_ids = [match.match_id for match in parsed_matches]
        match_map = {row.match_id: row for row in await self.match_repository.get_many_by_ids(db, match_ids)}

        inserted = 0
        updated = 0
        for match_create in parsed_matches:
            existing_match = match_map.get(match_create.match_id)
            db_data = match_create.model_dump()
            if existing_match:
                for key, value in db_data.items():
                    setattr(existing_match, key, value)
                updated += 1
            else:
                db.add(Match(**db_data))
                inserted += 1

        await db.flush()
        await self.cache_service.delete(make_cache_key("live_matches"))
        return {"success": True, "inserted": inserted, "updated": updated, "total": len(fixtures)}

    async def sync_full_season(self, db: AsyncSession, league: int, season: int) -> dict:
        result = await self.client.get("/fixtures", params={"league": league, "season": season})
        if not result or "response" not in result:
            return {"success": False, "message": "API error"}
        fixtures = result.get("response", [])
        if not fixtures:
            return {"success": False, "message": "No fixtures found"}
        return await self._process_sync(db, fixtures)

    async def sync_daily_fixtures(self, db: AsyncSession, target_date: str) -> dict:
        result = await self.client.get("/fixtures", params={"date": target_date})
        if not result or "response" not in result:
            return {"success": False, "message": "API error"}
        fixtures = result.get("response", [])
        if not fixtures:
            return {"success": True, "message": "No matches for today", "updated": 0}
        return await self._process_sync(db, fixtures)

    async def sync_live_matches(self, db: AsyncSession) -> dict:
        result = await self.client.get("/fixtures", params={"live": "all"})
        if not result or "response" not in result:
            return {"success": False, "message": "API error"}

        fixtures = result.get("response", [])
        api_live_ids = {item["fixture"]["id"] for item in fixtures if item.get("fixture") and item["fixture"].get("id")}
        stale_threshold = datetime.now(timezone.utc) - timedelta(hours=24)
        stale_matches = await self.match_repository.get_live_stale(db, api_live_ids, stale_threshold)
        logger.debug("get_live_stale returned %d stale matches", len(stale_matches))
        for match in stale_matches[:20]:
            logger.debug("fixture_id=%s status=%s match_time=%s", match.match_id, getattr(match, "status", None), getattr(match, "match_time", None))

        if stale_matches:
            stale_ids = [str(match.match_id) for match in stale_matches]
            logger.info("Syncing %d stale matches that are no longer in live feed", len(stale_ids))
            try:
                stale_chunks = [stale_ids[i : i + 20] for i in range(0, len(stale_ids), 20)]
                total_chunks = len(stale_chunks)
                stale_api_fixtures = []

                for chunk_index, chunk in enumerate(stale_chunks, start=1):
                    logger.info("Fetching stale chunk %s/%s (%s ids)", chunk_index, total_chunks, len(chunk))
                    stale_resp = await self.client.get("/fixtures", params={"ids": "-".join(chunk)})
                    chunk_response = stale_resp.get("response", []) if isinstance(stale_resp, dict) else []
                    logger.info("Chunk returned %s fixtures", len(chunk_response))

                    print("STALE_RESULTS =", stale_resp.get("results"))
                    print("STALE_ERRORS =", stale_resp.get("errors"))
                    print("STALE_RESPONSE_LEN =", len(chunk_response))
                    if chunk_response:
                        first = chunk_response[0]
                        print("FIRST_FIXTURE_ID =", first.get("fixture", {}).get("id"))
                        print("FIRST_STATUS =", first.get("fixture", {}).get("status", {}).get("short"))
                        print("FIRST_ELAPSED =", first.get("fixture", {}).get("status", {}).get("elapsed"))
                    print(f"STALE_API_COUNT={len(chunk_response)}")

                    stale_api_fixtures.extend(chunk_response)

                if stale_api_fixtures:
                    logger.info("total_stale_api_fixtures=%d", len(stale_api_fixtures))
                    for fixture in stale_api_fixtures:
                        fixture_info = fixture.get("fixture", {})
                        status_info = fixture_info.get("status", {})
                        logger.info(
                            "fixture_id=%s api_status=%s elapsed=%s",
                            fixture_info.get("id"),
                            status_info.get("short"),
                            status_info.get("elapsed"),
                        )
                    fixtures.extend(stale_api_fixtures)
            except Exception as exc:
                logger.error("Failed to fetch updates for stale matches: %s", exc)

        if not fixtures:
            return {"success": True, "message": "No live matches", "updated": 0}

        return await self._process_sync(db, fixtures)
