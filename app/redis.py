import redis
import redis.asyncio as aioredis

from app.core.config import settings


# Synchronous Redis client for sync request handlers
sync_redis = redis.from_url(
    settings.REDIS_URL,
    encoding="utf-8",
    decode_responses=True,
)

# Asynchronous Redis client for async service and route functions
async_redis = aioredis.from_url(
    settings.REDIS_URL,
    encoding="utf-8",
    decode_responses=True,
)
