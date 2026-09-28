import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache import make_cache_key
from app.models.match import Match
from app.models.match_h2h import MatchH2H
from app.providers.h2h_provider import H2HProvider
from app.repositories.team_repository import TeamRepository
from app.services.base.football_client import FootballAPIClient
from app.services.cache_service import CacheService
from app.services.h2h_sync_service import H2HSyncService

logger = logging.getLogger(__name__)


class H2HService:
    def __init__(
        self,
        client: FootballAPIClient,
        cache_service: CacheService | None = None,
        h2h_provider: H2HProvider | None = None,
        h2h_sync_service: H2HSyncService | None = None,
    ) -> None:
        self.client = client
        self.cache_service = cache_service or CacheService()
        self.h2h_provider = h2h_provider or H2HProvider(client)
        self.h2h_sync_service = h2h_sync_service or H2HSyncService(self, cache_service=self.cache_service, h2h_provider=self.h2h_provider)

    def _prepare_h2h_payload(self, payload: Optional[list[dict]]) -> Optional[list[dict]]:
        if not isinstance(payload, list):
            return payload

        def sort_key(item: object) -> float:
            if not isinstance(item, dict):
                return float("-inf")

            fixture = item.get("fixture")
            if not isinstance(fixture, dict):
                return float("-inf")

            timestamp = fixture.get("timestamp")
            if isinstance(timestamp, (int, float)):
                return float(timestamp)
            if isinstance(timestamp, str):
                try:
                    return float(timestamp)
                except ValueError:
                    return float("-inf")
            return float("-inf")

        return sorted(payload, key=sort_key, reverse=True)

    @staticmethod
    def _build_h2h_key(team1_id: int, team2_id: int) -> str:
        ids = sorted([int(team1_id), int(team2_id)])
        return f"{ids[0]}-{ids[1]}"

    async def get_match_h2h(self, provider_fixture_id: int) -> Optional[dict]:
        return await self.h2h_provider.get_match_h2h(provider_fixture_id)

    async def get_cached_h2h(
        self,
        db: AsyncSession,
        *args,
        match_id: int | None = None,
        team1_id: int | None = None,
        team2_id: int | None = None,
    ) -> Optional[dict]:
        if args:
            if len(args) == 1:
                match_id = int(args[0])
            elif len(args) == 2:
                team1_id, team2_id = int(args[0]), int(args[1])
            elif len(args) == 3:
                team1_id, team2_id, match_id = int(args[0]), int(args[1]), int(args[2])

        if match_id is not None:
            match = (await db.execute(select(Match).where(Match.match_id == match_id))).scalar_one_or_none()
            if match is not None:
                team1_id = int(getattr(match, "home_team_id", 0) or 0)
                team2_id = int(getattr(match, "away_team_id", 0) or 0)

        if team1_id is None or team2_id is None:
            return None

        local_h2h_key = self._build_h2h_key(team1_id, team2_id)
        cache_key = make_cache_key("match", "h2h", str(match_id) if match_id is not None else local_h2h_key)
        cached = await self.cache_service.get_json(cache_key)
        if cached is not None:
            return self._prepare_h2h_payload(cached)

        if match_id is None:
            return None

        team_repository = TeamRepository()
        home_team = await team_repository.get_by_id(db, team1_id)
        away_team = await team_repository.get_by_id(db, team2_id)
        if home_team is None or away_team is None:
            return None
        db_record = (await db.execute(select(MatchH2H).where(MatchH2H.h2h_key == local_h2h_key))).scalar_one_or_none()
        if db_record:
            prepared_payload = self._prepare_h2h_payload(db_record.data)
            # Read path remains read-only for H2H. Cache rebuilds are owned by
            # the explicit source-sync / post-commit invalidation lifecycle.
            return prepared_payload

        # Reads never trigger Analytics publication. Call refresh_h2h explicitly from a source owner.
        return None

    async def refresh_h2h(self, db: AsyncSession, match_id: int) -> dict:
        result = await self.h2h_sync_service.refresh_h2h(db, int(match_id))
        if "data" in result:
            result["data"] = self._prepare_h2h_payload(result["data"])
        return result
