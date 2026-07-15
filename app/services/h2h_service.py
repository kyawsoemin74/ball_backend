import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache import make_cache_key
from app.models.match import Match
from app.models.match_h2h import MatchH2H
from app.providers.h2h_provider import H2HProvider
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

    async def get_match_h2h(self, match_id: int) -> Optional[dict]:
        return await self.h2h_provider.get_match_h2h(match_id)

    async def get_cached_h2h(self, db: AsyncSession, team1_id: int, team2_id: int, match_id: int) -> Optional[dict]:
        ids = sorted([team1_id, team2_id])
        h2h_key = f"{ids[0]}-{ids[1]}"
        cache_key = make_cache_key("match", "h2h", h2h_key)

        cached = await self.cache_service.get_json(cache_key)
        if cached is not None:
            return cached

        match = (await db.execute(select(Match).where(Match.match_id == match_id))).scalar_one_or_none()
        if not match:
            return None

        db_record = (await db.execute(select(MatchH2H).where(MatchH2H.h2h_key == h2h_key))).scalar_one_or_none()
        if db_record:
            await self.cache_service.set_json(cache_key, db_record.data, 86400)
            return db_record.data

        refresh_result = await self.h2h_sync_service.refresh_h2h(db, h2h_key)
        if not refresh_result or "data" not in refresh_result:
            return None

        h2h_data = refresh_result["data"]
        await self.cache_service.set_json(cache_key, h2h_data, 86400)
        return h2h_data
