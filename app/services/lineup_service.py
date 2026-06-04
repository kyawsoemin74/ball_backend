import logging
from typing import List, Dict, Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.cache_service import CacheService
from app.models.match_lineup import MatchLineup
from app.services.base.football_client import FootballAPIClient

logger = logging.getLogger(__name__)


class LineupService:
    def __init__(self, client: FootballAPIClient, cache_service: CacheService | None = None) -> None:
        self.client = client
        self.cache_service = cache_service or CacheService()

    async def get_match_lineup(self, match_id: int) -> Optional[dict]:
        return await self.client.get("/fixtures/lineups", params={"fixture": match_id})

    async def get_cached_match_lineup(self, db: AsyncSession, match_id: int) -> Optional[List[Dict[str, Any]]]:
        from app.cache import make_cache_key

        cache_key = make_cache_key("match", match_id, "lineup")
        cached = await self.cache_service.get_json(cache_key)
        if cached is not None:
            return cached

        db_record = (await db.execute(select(MatchLineup).where(MatchLineup.match_id == match_id))).scalar_one_or_none()
        if db_record:
            await self.cache_service.set_json(cache_key, db_record.data, 3600)
            return db_record.data

        api_res = await self.get_match_lineup(match_id)
        if not api_res or "response" not in api_res:
            return None

        lineup_data = api_res["response"]
        if lineup_data:
            db.add(MatchLineup(match_id=match_id, data=lineup_data))
            await db.flush()
            await self.cache_service.set_json(cache_key, lineup_data, 3600)
        return lineup_data
