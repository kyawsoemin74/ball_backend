import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.cache import make_cache_key
from app.core.config import settings
from app.models.standing import Standings
from app.repositories.allowed_league_repository import AllowedLeagueRepository
from app.repositories.standing_repository import StandingRepository
from app.schemas.standing import StandingResponse
from app.services.base.football_client import FootballAPIClient
from app.services.cache_service import CacheService
from app.services.team_service import TeamService

logger = logging.getLogger(__name__)


class StandingService:
    def __init__(self, client: FootballAPIClient, team_service: TeamService, cache_service: CacheService | None = None) -> None:
        self.client = client
        self.team_service = team_service
        self.cache_service = cache_service or CacheService()
        self.standing_repository = StandingRepository()
        self.allowed_league_repository = AllowedLeagueRepository()

    async def get_league_standings(self, league_id: int, season: int) -> Optional[dict]:
        return await self.client.get("/standings", params={"league": league_id, "season": season})

    async def upsert_standings(self, db: AsyncSession, standings_data: list, league_id: int, season: str):
        team_payload = []
        for standing in standings_data:
            team = standing.get("team") or {}
            if not isinstance(team, dict):
                logger.warning("Skipping standings row with invalid team payload: %r", standing)
                continue
            team_id = team.get("id")
            if team_id is None:
                logger.warning("Skipping standings row with missing team_id: %r", standing)
                continue
            if not team.get("name"):
                logger.warning("Skipping standings row with missing team name for team_id=%s", team_id)
                continue
            team_payload.append({"team_id": int(team_id), "name": team.get("name"), "logo": team.get("logo"), "country": team.get("country")})

        await self.team_service.ensure_teams_exist(db, team_payload)
        logger.info("Standings sync ensured %s teams before insert", len(team_payload))

        await self.standing_repository.delete_for_league_season(db, league_id, season)
        await db.flush()

        for standing in standings_data:
            goals_for = standing.get("all", {}).get("goals", {}).get("for", 0)
            goals_against = standing.get("all", {}).get("goals", {}).get("against", 0)
            db.add(Standings(
                league_id=league_id,
                season=str(season),
                team_id=standing["team"]["id"],
                position=standing["rank"],
                team_name=standing["team"]["name"],
                team_logo=standing["team"]["logo"],
                points=standing["points"],
                played=standing["all"]["played"],
                won=standing["all"]["win"],
                drawn=standing["all"]["draw"],
                lost=standing["all"]["lose"],
                goals_for=goals_for,
                goals_against=goals_against,
                goal_difference=standing.get("goalsDiff", goals_for - goals_against),
            ))

        await db.flush()
        self.cache_service.delete_sync(make_cache_key("standings", league_id, season))

    async def sync_standings(self, db: AsyncSession, league_id: int, season: int) -> dict:
        allowed_ids = await self.allowed_league_repository.get_allowed_ids(db)
        if not allowed_ids:
            logger.info("Allowed league list is empty; skipping standings synchronization for league_id=%s", league_id)
            return {"success": True, "league_id": league_id, "season": season, "updated": 0, "message": "League is not allowed for synchronization"}

        if league_id not in allowed_ids:
            logger.info("SKIPPED LEAGUE: league_id=%s league_name=%s", league_id, "requested league")
            return {"success": True, "league_id": league_id, "season": season, "updated": 0, "message": "League is not allowed for synchronization"}

        logger.info("ALLOWED LEAGUE: league_id=%s league_name=%s", league_id, "requested league")
        result = await self.get_league_standings(league_id, season)
        if not result or "response" not in result or not result["response"]:
            return {"success": False, "message": "No standings data found from API"}

        try:
            standings_list = result["response"][0]["league"]["standings"][0]
            await self.upsert_standings(db, standings_list, league_id, str(season))
            return {"success": True, "league_id": league_id, "season": season, "updated": len(standings_list)}
        except (KeyError, IndexError) as exc:
            logger.error("Error parsing standings response: %s", exc)
            return {"success": False, "message": "Unexpected API response format"}

    async def get_cached_standings(self, db: AsyncSession, league_id: int, season: int | str) -> Optional[list]:
        cache_key = make_cache_key("standings", league_id, season)
        cached = await self.cache_service.get_json(cache_key)
        if cached is not None:
            return cached

        standings_rows = await self.standing_repository.get_for_league_season(db, league_id, season)
        if standings_rows:
            latest_update = max((row.updated_at for row in standings_rows if row.updated_at), default=None)
            if latest_update and (datetime.now(timezone.utc) - latest_update) < timedelta(seconds=int(settings.REDIS_TTL_STANDINGS)):
                payload = [StandingResponse.model_validate(row).model_dump(mode="json") for row in standings_rows]
                await self.cache_service.set_json(cache_key, payload, settings.REDIS_TTL_STANDINGS)
                return payload

            api_res = await self.get_league_standings(league_id, int(season))
            if api_res and "response" in api_res and api_res["response"]:
                try:
                    standings_data = api_res["response"][0]["league"]["standings"][0]
                    await self.upsert_standings(db, standings_data, league_id, str(season))
                    rows = await self.standing_repository.get_for_league_season(db, league_id, season)
                    payload = [StandingResponse.model_validate(row).model_dump(mode="json") for row in rows]
                    await self.cache_service.set_json(cache_key, payload, settings.REDIS_TTL_STANDINGS)
                    return payload
                except Exception as exc:
                    logger.error("Error refreshing standings for league %s season %s: %s", league_id, season, exc)

            payload = [StandingResponse.model_validate(row).model_dump(mode="json") for row in standings_rows]
            await self.cache_service.set_json(cache_key, payload, settings.REDIS_TTL_STANDINGS)
            return payload

        api_res = await self.get_league_standings(league_id, int(season))
        if not api_res or "response" not in api_res or not api_res["response"]:
            return None

        try:
            standings_data = api_res["response"][0]["league"]["standings"][0]
            await self.upsert_standings(db, standings_data, league_id, str(season))
            rows = await self.standing_repository.get_for_league_season(db, league_id, season)
            payload = [StandingResponse.model_validate(row).model_dump(mode="json") for row in rows]
            await self.cache_service.set_json(cache_key, payload, settings.REDIS_TTL_STANDINGS)
            return payload
        except Exception as exc:
            logger.error("Error fetching/upserting standings for league %s season %s: %s", league_id, season, exc)
            return None
