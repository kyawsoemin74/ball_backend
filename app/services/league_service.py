import logging
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.league import League
from app.providers.league_provider import LeagueProvider
from app.repositories.allowed_league_repository import AllowedLeagueRepository
from app.repositories.league_repository import LeagueRepository
from app.services.base.football_client import FootballAPIClient
from app.services.cache_service import CacheService
from app.services.league_sync_service import LeagueSyncService

logger = logging.getLogger(__name__)


class LeagueService:
    def __init__(
        self,
        client: FootballAPIClient,
        cache_service: CacheService | None = None,
        league_provider: LeagueProvider | None = None,
        league_sync_service: LeagueSyncService | None = None,
    ) -> None:
        self.client = client
        self.league_provider = league_provider or LeagueProvider(client)
        self.cache_service = cache_service or CacheService()
        self._league_repository = LeagueRepository()
        self._allowed_league_repository = AllowedLeagueRepository()
        self.league_sync_service = league_sync_service or LeagueSyncService(
            cache_service=self.cache_service,
            league_repository=self._league_repository,
            allowed_league_repository=self._allowed_league_repository,
            fetch_all_leagues=self.get_all_leagues,
        )
        self._league_sync_upsert_impl = self.league_sync_service.upsert_league
        self.league_sync_service.upsert_league = self._upsert_league_bridge
        self.league_repository = self._league_repository
        self.allowed_league_repository = self._allowed_league_repository

    async def _upsert_league_bridge(self, db: AsyncSession, league_data: dict, allowed_ids: set[int] | None = None) -> League | None:
        # Preserve pre-refactor compatibility so overrides on LeagueService.upsert_league still intercept sync writes.
        return await self.upsert_league(db, league_data, allowed_ids=allowed_ids)

    @staticmethod
    def _ensure_repository_write_compat(repository) -> None:
        if hasattr(repository, "upsert_one"):
            return

        async def _compat_upsert_one(db: AsyncSession, row: dict) -> League:
            league_id = int(row["league_id"])
            existing = await repository.get_by_id(db, league_id)
            if existing:
                existing.name = row["name"]
                existing.country = row["country"]
                existing.country_code = row.get("country_code")
                existing.logo = row.get("logo")
                existing.season = row.get("season")
                existing.is_featured = bool(row.get("is_featured", existing.is_featured))
                existing.display_order = int(row.get("display_order", existing.display_order))
                if db is not None:
                    await db.flush()
                return existing

            new_league = League(
                league_id=league_id,
                name=row["name"],
                country=row.get("country"),
                country_code=row.get("country_code"),
                logo=row.get("logo"),
                season=row.get("season"),
                is_featured=bool(row.get("is_featured", False)),
                display_order=int(row.get("display_order", 999)),
            )
            if db is not None:
                db.add(new_league)
                await db.flush()
                if hasattr(db, "refresh"):
                    await db.refresh(new_league)
            return new_league

        setattr(repository, "upsert_one", _compat_upsert_one)

    @property
    def league_repository(self):
        return self._league_repository

    @league_repository.setter
    def league_repository(self, value):
        self._ensure_repository_write_compat(value)
        self._league_repository = value
        self.league_sync_service.league_repository = value

    @property
    def allowed_league_repository(self):
        return self._allowed_league_repository

    @allowed_league_repository.setter
    def allowed_league_repository(self, value):
        self._allowed_league_repository = value
        self.league_sync_service.allowed_league_repository = value

    async def get_cached_league_top_scorers(self, league_id: int, season: int) -> Optional[dict]:
        from app.cache import make_cache_key

        cache_key = make_cache_key("league", league_id, "topscorers", season)
        cached = await self.cache_service.get_json(cache_key)
        if cached is not None:
            return cached

        result = await self.league_provider.get_league_top_scorers(league_id, season)
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
        await self.cache_service.set_json(cache_key, payload, settings.REDIS_TTL_LEAGUE_TOP_SCORERS)
        return payload

    async def get_league_details(self, league_id: int) -> Optional[dict]:
        return await self.league_provider.get_league_details(league_id)

    async def get_all_leagues(self) -> Optional[dict]:
        return await self.league_provider.get_all_leagues()

    async def upsert_league(self, db: AsyncSession, league_data: dict, allowed_ids: set[int] | None = None) -> League | None:
        return await self._league_sync_upsert_impl(db, league_data, allowed_ids=allowed_ids)

    async def sync_all_leagues(self, db: AsyncSession) -> dict:
        return await self.league_sync_service.sync_all_leagues(db)
