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

    async def get_match_h2h(self, match_id: int) -> Optional[dict]:
        return await self.h2h_provider.get_match_h2h(match_id)

    async def get_cached_h2h(self, db: AsyncSession, team1_id: int, team2_id: int, match_id: int) -> Optional[dict]:
        ids = sorted([team1_id, team2_id])
        h2h_key = f"{ids[0]}-{ids[1]}"
        cache_key = make_cache_key("match", "h2h", h2h_key)

        cached = await self.cache_service.get_json(cache_key)
        if cached is not None:
            return self._prepare_h2h_payload(cached)

        match = (await db.execute(select(Match).where(Match.match_id == match_id))).scalar_one_or_none()
        if not match:
            return None

        team_repository = TeamRepository()
        home_team = await team_repository.get_by_id(db, team1_id)
        away_team = await team_repository.get_by_id(db, team2_id)
        if home_team is None or away_team is None:
            return None
        home_provider_id = getattr(home_team, "provider_id", None)
        away_provider_id = getattr(away_team, "provider_id", None)
        if home_provider_id is None or away_provider_id is None:
            return None
        provider_h2h_key = "-".join(sorted([str(home_provider_id), str(away_provider_id)], key=int))

        db_record = (await db.execute(select(MatchH2H).where(MatchH2H.h2h_key == h2h_key))).scalar_one_or_none()
        if db_record:
            prepared_payload = self._prepare_h2h_payload(db_record.data)
            # Read path remains read-only for H2H. Cache rebuilds are owned by
            # the explicit source-sync / post-commit invalidation lifecycle.
            return prepared_payload

        # Reads never trigger Analytics publication. Call refresh_h2h explicitly from a source owner.
        return None

    async def refresh_h2h(self, db: AsyncSession, team1_id: int, team2_id: int) -> dict:
        teams = await self._resolve_provider_team_ids(db, team1_id, team2_id)
        provider_h2h_key = "-".join(sorted([str(teams[0]), str(teams[1])], key=int))
        result = await self.h2h_sync_service.refresh_h2h(db, provider_h2h_key)
        if "data" in result:
            result["data"] = self._prepare_h2h_payload(result["data"])
        return result

    async def _resolve_provider_team_ids(self, db: AsyncSession, team1_id: int, team2_id: int) -> tuple[int, int]:
        repository = TeamRepository()
        first = await repository.get_by_id(db, team1_id)
        second = await repository.get_by_id(db, team2_id)
        if first is None or second is None or getattr(first, "provider_id", None) is None or getattr(second, "provider_id", None) is None:
            raise ValueError("unresolved Team identity for H2H refresh")
        return int(first.provider_id), int(second.provider_id)
