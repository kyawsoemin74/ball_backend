import logging
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.cache import make_cache_key
from app.models.league import League
from app.repositories.league_repository import LeagueRepository
from app.services.base.football_client import FootballAPIClient
from app.services.cache_service import CacheService

logger = logging.getLogger(__name__)


class LeagueService:
    def __init__(self, client: FootballAPIClient, cache_service: CacheService | None = None) -> None:
        self.client = client
        self.cache_service = cache_service or CacheService()
        self.league_repository = LeagueRepository()

    async def get_league_details(self, league_id: int) -> Optional[dict]:
        return await self.client.get("/leagues", params={"id": league_id})

    async def get_all_leagues(self) -> Optional[dict]:
        return await self.client.get("/leagues")

    async def upsert_league(self, db: AsyncSession, league_data: dict) -> League:
        league_payload = league_data.get("league") or league_data
        league_id = league_payload.get("id")
        if league_id is None:
            raise ValueError("League payload is missing the id field")

        country_payload = league_data.get("country") or {}
        if isinstance(country_payload, dict):
            country_name = country_payload.get("name") or country_payload.get("country")
        else:
            country_name = country_payload

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
            existing.logo = league_payload.get("logo", existing.logo)
            existing.season = str(season_value) if season_value is not None else existing.season
            if "is_featured" in league_payload:
                existing.is_featured = bool(league_payload.get("is_featured", existing.is_featured))
            if "display_order" in league_payload:
                existing.display_order = int(league_payload.get("display_order", existing.display_order))
            await db.flush()
            self.cache_service.delete_sync(make_cache_key("league", league_id))
            return existing

        new_league = League(
            league_id=league_id,
            name=league_payload.get("name"),
            country=country_name,
            logo=league_payload.get("logo"),
            season=str(season_value) if season_value is not None else None,
            is_featured=bool(league_payload.get("is_featured", False)),
            display_order=int(league_payload.get("display_order", 999)),
        )
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
        league_ids = []
        for league_data in leagues:
            league_payload = league_data.get("league") or league_data
            league_id = league_payload.get("id")
            if league_id is None:
                logger.warning("Skipping invalid league payload: %s", league_data)
                continue
            league_ids.append(int(league_id))

        existing_lookup = {league.league_id: league for league in await self.league_repository.get_many_by_ids(db, league_ids)}

        inserted = 0
        updated = 0
        for league_data in leagues:
            league_payload = league_data.get("league") or league_data
            league_id = league_payload.get("id")
            if league_id is None:
                continue

            await self.upsert_league(db, league_data)
            if league_id in existing_lookup:
                updated += 1
            else:
                inserted += 1
        logger.info("League sync completed")
        logger.info("League sync result: inserted=%s, updated=%s", inserted, updated)
        return {"success": True, "inserted": inserted, "updated": updated, "total": len(leagues)}
