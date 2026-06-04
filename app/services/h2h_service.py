import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache import make_cache_key
from app.models.match import Match
from app.models.match_h2h import MatchH2H
from app.services.base.football_client import FootballAPIClient
from app.services.cache_service import CacheService

logger = logging.getLogger(__name__)


class H2HService:
    def __init__(self, client: FootballAPIClient, cache_service: CacheService | None = None) -> None:
        self.client = client
        self.cache_service = cache_service or CacheService()

    async def get_match_h2h(self, match_id: int) -> Optional[dict]:
        return await self.client.get("/fixtures/headtohead", params={"fixture": match_id})

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

        api_res = await self.client.get("/fixtures/headtohead", params={"h2h": h2h_key})
        if not api_res or "response" not in api_res:
            return None

        h2h_data = api_res["response"]
        existing_record = (await db.execute(select(MatchH2H).where(MatchH2H.h2h_key == h2h_key))).scalar_one_or_none()
        if existing_record:
            existing_record.data = h2h_data
        else:
            db.add(MatchH2H(h2h_key=h2h_key, data=h2h_data))
        await db.flush()
        await self.cache_service.set_json(cache_key, h2h_data, 86400)
        return h2h_data
