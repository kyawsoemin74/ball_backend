import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache import make_cache_key
from app.core.config import settings
from app.models.match_lineup import MatchLineup
from app.providers.lineup_provider import LineupProvider
from app.services.base.football_client import FootballAPIClient
from app.services.cache_service import CacheService
from app.services.lineup_sync_service import LineupSyncService

logger = logging.getLogger(__name__)


def make_lineup_cache_key(match_id: int) -> str:
    return make_cache_key("lineup", match_id)


class LineupService:
    def __init__(
        self,
        client: FootballAPIClient,
        cache_service: object | None = None,
        lineup_provider: LineupProvider | None = None,
        lineup_sync_service: LineupSyncService | None = None,
    ) -> None:
        self.client = client
        self.cache_service = cache_service or CacheService()
        self.lineup_provider = lineup_provider or LineupProvider(client)
        self.lineup_sync_service = lineup_sync_service or LineupSyncService(lineup_provider=self.lineup_provider)

    def _is_valid_lineup_response(self, lineup_data: Any) -> bool:
        if not isinstance(lineup_data, list) or not lineup_data:
            return False

        for lineup in lineup_data:
            if not isinstance(lineup, dict):
                return False

            team = lineup.get("team")
            if not isinstance(team, dict) or not team.get("id"):
                return False

            if not isinstance(lineup.get("startXI"), list):
                return False

            if not isinstance(lineup.get("substitutes"), list):
                return False

        return True

    async def get_match_lineup(self, match_id: int) -> Optional[dict]:
        return None

    async def sync_lineup(self, db: AsyncSession, match_id: int) -> Dict[str, Any]:
        cache_key = make_lineup_cache_key(match_id)
        sync_result = await self.lineup_sync_service.sync_lineup(
            db=db,
            match_id=match_id,
            validate_lineup=self._is_valid_lineup_response,
            cache_service=self.cache_service,
            cache_key=cache_key,
        )
        if sync_result.get("success") and not sync_result.get("skipped"):
            await self.cache_service.delete(cache_key)
            logger.debug("LINEUP_CACHE_DELETE", extra={"match_id": match_id})
        return sync_result

    async def get_cached_match_lineup(self, db: AsyncSession, match_id: int) -> Optional[List[Dict[str, Any]]]:
        cache_key = make_lineup_cache_key(match_id)
        cached = await self.cache_service.get_json(cache_key)
        if cached is not None:
            logger.debug("LINEUP_CACHE_HIT", extra={"match_id": match_id})
            return cached

        logger.debug("LINEUP_CACHE_MISS", extra={"match_id": match_id})
        db_record = (await db.execute(select(MatchLineup).where(MatchLineup.match_id == match_id))).scalar_one_or_none()
        if db_record:
            await self.cache_service.set_json(cache_key, db_record.data, settings.REDIS_TTL_LINEUP)
            logger.debug("LINEUP_CACHE_SET", extra={"match_id": match_id})
            return db_record.data

        return None
