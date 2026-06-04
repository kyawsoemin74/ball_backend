from typing import Any

from app.cache import cache_delete, cache_delete_sync, cache_get_json, cache_set_json
from app.redis import async_redis


class CacheService:
    """Thin cache facade used by the service layer."""

    async def get_json(self, key: str) -> Any:
        return await cache_get_json(key)

    async def set_json(self, key: str, value: Any, ttl: int) -> None:
        await cache_set_json(key, value, ttl)

    async def delete(self, key: str) -> None:
        await cache_delete(key)

    def delete_sync(self, key: str) -> None:
        cache_delete_sync(key)

    async def invalidate_pattern(self, pattern: str) -> int:
        deleted = 0
        async for key in async_redis.scan_iter(match=pattern):
            await async_redis.delete(key)
            deleted += 1
        return deleted
