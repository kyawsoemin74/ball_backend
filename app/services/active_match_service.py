import logging
from typing import List

from app.cache import make_active_match_key, make_cache_key
from app.core.config import settings
from app.redis import async_redis

logger = logging.getLogger(__name__)


class ActiveMatchService:
    async def mark_match_active(self, match_id: int) -> int:
        logger.info("ACTIVE_MATCH_HEARTBEAT", extra={"match_id": match_id})
        key = make_active_match_key(match_id)
        ttl = settings.ACTIVE_MATCH_TTL_SECONDS
        await async_redis.set(key, "1", ex=ttl)
        logger.info("ACTIVE_MATCH_REGISTERED", extra={"match_id": match_id, "ttl": ttl})
        return ttl

    async def get_active_matches(self) -> List[int]:
        pattern = make_cache_key("active_match", "*")
        match_ids: List[int] = []

        async for key in async_redis.scan_iter(match=pattern):
            value = await async_redis.get(key)
            match_id_str = key.rsplit(":", 1)[-1]

            if value is None:
                logger.info("ACTIVE_MATCH_EXPIRED", extra={"match_id": match_id_str})
                continue

            try:
                match_ids.append(int(match_id_str))
            except ValueError:
                continue

        return sorted(set(match_ids))


active_match_service = ActiveMatchService()
