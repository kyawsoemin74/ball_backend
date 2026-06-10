import logging
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.base.football_client import FootballAPIClient
from app.services.cache_service import CacheService

logger = logging.getLogger(__name__)


class StatisticsService:
    def __init__(self, client: FootballAPIClient, cache_service: CacheService | None = None) -> None:
        self.client = client
        self.cache_service = cache_service or CacheService()

    async def get_match_statistics(self, match_id: int) -> Optional[dict]:
        return await self.client.get("/fixtures/statistics", params={"fixture": match_id})

    async def get_cached_statistics(self, db: AsyncSession, match_id: int) -> dict:
        from app.cache import make_cache_key

        cache_key = make_cache_key("match", match_id, "statistics")
        cached = await self.cache_service.get_json(cache_key)
        if cached is not None:
            return cached

        api_res = await self.get_match_statistics(match_id)
        if not api_res or "response" not in api_res:
            return {"error": "Statistics not found"}

        statistics_data = api_res.get("response")
        if not statistics_data:
            return {"error": "Statistics not found"}

        await self.cache_service.set_json(cache_key, api_res, 3600)
        return api_res
