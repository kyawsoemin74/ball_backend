import logging
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.cache import make_cache_key
from app.models.league import League
from app.repositories.allowed_league_repository import AllowedLeagueRepository
from app.repositories.league_repository import LeagueRepository
from app.services.base.football_client import FootballAPIClient
from app.services.cache_service import CacheService

logger = logging.getLogger(__name__)


class LeagueService:
    def __init__(self, client: FootballAPIClient, cache_service: CacheService | None = None) -> None:
        self.client = client
        self.cache_service = cache_service or CacheService()
        self.league_repository = LeagueRepository()
        self.allowed_league_repository = AllowedLeagueRepository()

    async def get_cached_league_top_scorers(self, league_id: int, season: int) -> Optional[dict]:
        from app.cache import make_cache_key

        cache_key = make_cache_key("league", league_id, "topscorers", season)
        cached = await self.cache_service.get_json(cache_key)
        if cached is not None:
            return cached

        result = await self.client.get("/players/topscorers", params={"league": league_id, "season": season})
        if not result or "response" not in result or not result["response"]:
            return {"error": "Top scorers not found"}

        payload = {
            "league_id": league_id,
            "season": season,
            "players": [
                {
                    "player_id": item.get("player", {}).get("id"),
                    "player_name": item.get("player", {}).get("name"),
                    "team_id": item.get("statistics", [{}])[0].get("team", {}).get("id") if isinstance(item.get("statistics"), list) and item.get("statistics") else None,
                    "team_name": item.get("statistics", [{}])[0].get("team", {}).get("name") if isinstance(item.get("statistics"), list) and item.get("statistics") else None,
                    "goals": item.get("statistics", [{}])[0].get("goals", {}).get("total") if isinstance(item.get("statistics"), list) and item.get("statistics") else None,
                    "assists": item.get("statistics", [{}])[0].get("goals", {}).get("assists") if isinstance(item.get("statistics"), list) and item.get("statistics") else None,
                    "appearances": item.get("statistics", [{}])[0].get("games", {}).get("appearences") if isinstance(item.get("statistics"), list) and item.get("statistics") else None,
                    "photo": item.get("player", {}).get("photo"),
                }
                for item in result["response"]
                if isinstance(item, dict)
            ],
        }
        await self.cache_service.set_json(cache_key, payload, 300)
        return payload

    async def get_league_details(self, league_id: int) -> Optional[dict]:
        return await self.client.get("/leagues", params={"id": league_id})

    async def get_all_leagues(self) -> Optional[dict]:
        return await self.client.get("/leagues")

    async def upsert_league(self, db: AsyncSession, league_data: dict, allowed_ids: set[int] | None = None) -> League | None:
        league_payload = league_data.get("league") or league_data
        league_id = league_payload.get("id")
        if league_id is None:
            raise ValueError("League payload is missing the id field")

        if allowed_ids is not None and (not allowed_ids or int(league_id) not in allowed_ids):
            logger.info("SKIPPED LEAGUE: league_id=%s league_name=%s", league_id, league_payload.get("name"))
            return None

        return await self._upsert_league(db, league_data)

    async def _upsert_league(self, db: AsyncSession, league_data: dict) -> League | None:
        league_payload = league_data.get("league") or league_data
        league_id = league_payload.get("id")
        if league_id is None:
            raise ValueError("League payload is missing the id field")

        country_payload = league_data.get("country")
        if isinstance(country_payload, dict):
            country_name = country_payload.get("name") or country_payload.get("country")
            country_code = country_payload.get("code")
        else:
            country_name = country_payload or league_payload.get("country")
            country_code = None

        season_value = None
        for season in league_data.get("seasons") or []:
            if isinstance(season, dict):
                season_value = season.get("year") or season.get("season")
            else:
                season_value = season
            break

        existing = await self.league_repository.get_by_id(db, league_id)
        if existing:
            existing.name = league_payload.get("name", existing.name)
            existing.country = country_name or existing.country
            existing.country_code = country_code or existing.country_code
            existing.logo = league_payload.get("logo", existing.logo)
            existing.season = str(season_value) if season_value is not None else existing.season
            if "is_featured" in league_payload:
                existing.is_featured = bool(league_payload.get("is_featured", existing.is_featured))
            if "display_order" in league_payload:
                existing.display_order = int(league_payload.get("display_order", existing.display_order))
            if db is not None:
                await db.flush()
            self.cache_service.delete_sync(make_cache_key("league", league_id))
            return existing

        new_league = League(
            league_id=league_id,
            name=league_payload.get("name"),
            country=country_name,
            country_code=country_code,
            logo=league_payload.get("logo"),
            season=str(season_value) if season_value is not None else None,
            is_featured=bool(league_payload.get("is_featured", False)),
            display_order=int(league_payload.get("display_order", 999)),
        )
        if db is not None:
            db.add(new_league)
            await db.flush()
            await db.refresh(new_league)
        self.cache_service.delete_sync(make_cache_key("league", league_id))
        return new_league

    async def sync_all_leagues(self, db: AsyncSession) -> dict:
        logger.info("League sync started")
        result = await self.get_all_leagues()
        if not result or "response" not in result:
            logger.warning("League sync aborted: no response from API-Football")
            return {"success": False, "message": "No leagues data found from API"}

        leagues = result.get("response", [])
        allowed_ids = await self.allowed_league_repository.get_allowed_ids(db)
        if not allowed_ids:
            logger.info("Allowed league list is empty; skipping all league synchronization.")
            return {"success": True, "inserted": 0, "updated": 0, "total": 0}

        filtered_leagues = []
        for league_data in leagues:
            league_payload = league_data.get("league") or league_data
            league_id = league_payload.get("id")
            if league_id is None:
                logger.warning("Skipping invalid league payload: %s", league_data)
                continue

            league_id_int = int(league_id)
            if league_id_int not in allowed_ids:
                logger.info("SKIPPED LEAGUE: league_id=%s league_name=%s", league_id_int, league_payload.get("name"))
                continue

            logger.info("ALLOWED LEAGUE: league_id=%s league_name=%s", league_id_int, league_payload.get("name"))
            filtered_leagues.append(league_data)

        if not filtered_leagues:
            logger.info("No allowed leagues were present in the API response; skipping league synchronization.")
            return {"success": True, "inserted": 0, "updated": 0, "total": 0}

        league_ids = [int((league_data.get("league") or league_data).get("id")) for league_data in filtered_leagues]
        existing_lookup = {league.league_id: league for league in await self.league_repository.get_many_by_ids(db, league_ids)}

        inserted = 0
        updated = 0
        for league_data in filtered_leagues:
            league_payload = league_data.get("league") or league_data
            league_id = int(league_payload.get("id"))

            upserted = await self.upsert_league(db, league_data, allowed_ids=allowed_ids)
            if upserted is None:
                continue
            if league_id in existing_lookup:
                updated += 1
            else:
                inserted += 1
        logger.info("League sync completed")
        logger.info("League sync result: inserted=%s, updated=%s", inserted, updated)
        return {"success": True, "inserted": inserted, "updated": updated, "total": len(filtered_leagues)}
