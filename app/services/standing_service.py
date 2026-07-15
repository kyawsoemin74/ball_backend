import logging
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.cache import make_cache_key
from app.core.config import settings
from app.models.team import Team
from app.providers.standing_provider import StandingProvider
from app.repositories.standing_repository import StandingRepository
from app.repositories.team_repository import TeamRepository
from app.schemas.standing import StandingResponse
from app.services.base.football_client import FootballAPIClient
from app.services.cache_service import CacheService
from app.services.standing_sync_service import StandingSyncService
from app.services.team_service import TeamService

logger = logging.getLogger(__name__)


class StandingService:
    def __init__(
        self,
        client: FootballAPIClient,
        team_service: TeamService,
        cache_service: CacheService | None = None,
        standing_provider: StandingProvider | None = None,
    ) -> None:
        self.standing_provider = standing_provider or StandingProvider(client)
        self.team_service = team_service
        self.cache_service = cache_service or CacheService()
        self._standing_repository = StandingRepository()
        self._allowed_league_repository = None
        self.standing_sync_service = StandingSyncService(
            standing_provider=self.standing_provider,
            team_service=self.team_service,
            cache_service=self.cache_service,
        )
        self._standing_sync_upsert_impl = self.standing_sync_service.upsert_standings
        self.standing_sync_service.upsert_standings = self._upsert_standings_bridge
        self.standing_repository = self._standing_repository
        self.allowed_league_repository = self.standing_sync_service.allowed_league_repository

    async def _upsert_standings_bridge(self, db: AsyncSession, standings_data: list, league_id: int, season: str):
        # Preserve pre-refactor compatibility: sync path calls through StandingService.upsert_standings
        # so subclasses overriding upsert_standings still intercept writes.
        return await self.upsert_standings(db, standings_data, league_id, season)

    @property
    def standing_repository(self):
        return self._standing_repository

    @standing_repository.setter
    def standing_repository(self, value):
        self._standing_repository = value
        self.standing_sync_service.standing_repository = value

    @property
    def allowed_league_repository(self):
        return self._allowed_league_repository

    @allowed_league_repository.setter
    def allowed_league_repository(self, value):
        self._allowed_league_repository = value
        self.standing_sync_service.allowed_league_repository = value

    async def get_league_standings(self, league_id: int, season: int) -> Optional[dict]:
        return await self.standing_provider.get_league_standings(league_id, season)

    def _flatten_standings_groups(self, api_result: dict) -> list:
        return self.standing_sync_service._flatten_standings_groups(api_result)

    def _prepare_standings_rows(self, standings_data: list) -> list[dict]:
        return self.standing_sync_service._prepare_standings_rows(standings_data)

    async def upsert_standings(self, db: AsyncSession, standings_data: list, league_id: int, season: str):
        self.standing_sync_service._defer_standings_cache_invalidation = getattr(self, "_defer_standings_cache_invalidation", False)
        return await self._standing_sync_upsert_impl(db, standings_data, league_id, season)

    async def sync_standings(self, db: AsyncSession, league_id: int, season: int) -> dict:
        self.standing_sync_service._defer_standings_cache_invalidation = getattr(self, "_defer_standings_cache_invalidation", False)
        return await self.standing_sync_service.sync_standings(db, league_id, season)

    async def get_cached_standings(self, db: AsyncSession, league_id: int, season: int | str) -> Optional[list]:
        cache_key = make_cache_key("standings", league_id, season)
        cached = await self.cache_service.get_json(cache_key)
        if cached is not None:
            return cached

        standings_rows = await self.standing_repository.get_for_league_season(db, league_id, season)
        if standings_rows:
            payload = [StandingResponse.model_validate(row).model_dump(mode="json") for row in standings_rows]
            await self.cache_service.set_json(cache_key, payload, settings.REDIS_TTL_STANDINGS)
            return payload

        return None

    async def get_team_profile_standings(self, db: AsyncSession, team_id: int) -> Optional[list]:
        team_repository = TeamRepository()
        team = await team_repository.get_by_id(db, team_id)
        if team is None:
            return None

        current_league_id = getattr(team, "current_league_id", None)
        current_season = getattr(team, "current_season", None)
        if current_league_id is None or current_season is None:
            return None

        cache_key = make_cache_key("standings", current_league_id, current_season)
        cached = await self.cache_service.get_json(cache_key)
        if cached is not None:
            return cached

        standings_rows = await self.standing_repository.get_for_league_season(db, current_league_id, current_season)
        if not standings_rows:
            return None

        payload = [StandingResponse.model_validate(row).model_dump(mode="json") for row in standings_rows]
        await self.cache_service.set_json(cache_key, payload, settings.REDIS_TTL_STANDINGS)
        return payload
