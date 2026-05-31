import json
from typing import Any, Optional

from fastapi.encoders import jsonable_encoder

from app.core.config import settings
from app.monitoring import CACHE_HITS, CACHE_MISSES
from app.redis import async_redis, sync_redis


def make_cache_key(*parts: Any) -> str:
    return ":".join(
        [settings.REDIS_CACHE_PREFIX] + [str(part) for part in parts]
    )


def _serialize(value: Any) -> str:
    return json.dumps(
        jsonable_encoder(value),
        separators=(",", ":"),
        sort_keys=True,
    )


def _deserialize(value: Optional[str]) -> Any:
    if value is None:
        return None
    return json.loads(value)


# ---------------------------
# Sync Cache Functions
# ---------------------------

def cache_get_json_sync(key: str) -> Any:
    value = sync_redis.get(key)

    deserialized = _deserialize(value)

    if deserialized is None:
        CACHE_MISSES.inc()
    else:
        CACHE_HITS.inc()

    return deserialized


def cache_set_json_sync(
    key: str,
    value: Any,
    ttl: int,
) -> None:
    sync_redis.set(
        key,
        _serialize(value),
        ex=ttl,
    )


def cache_delete_sync(key: str) -> None:
    sync_redis.delete(key)


# ---------------------------
# Async Cache Functions
# ---------------------------

async def cache_get_json(key: str) -> Any:
    value = await async_redis.get(key)

    deserialized = _deserialize(value)

    if deserialized is None:
        CACHE_MISSES.inc()
    else:
        CACHE_HITS.inc()

    return deserialized


async def cache_set_json(
    key: str,
    value: Any,
    ttl: int,
) -> None:
    await async_redis.set(
        key,
        _serialize(value),
        ex=ttl,
    )


async def cache_delete(key: str) -> None:
    await async_redis.delete(key)