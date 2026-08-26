import json
import logging
from typing import Any, Optional

from fastapi.encoders import jsonable_encoder

from app.core.config import settings
from app.monitoring import (
    CACHE_DELETE_FAILURES,
    CACHE_DESERIALIZE_FAILURES,
    CACHE_GET_FAILURES,
    CACHE_HITS,
    CACHE_MISSES,
    CACHE_SET_FAILURES,
)
from app.redis import async_redis, sync_redis

logger = logging.getLogger(__name__)


def make_cache_key(*parts: Any) -> str:
    return ":".join(
        [settings.REDIS_CACHE_PREFIX] + [str(part) for part in parts]
    )


def make_active_match_key(match_id: int) -> str:
    return make_cache_key("active_match", match_id)


def _serialize(value: Any) -> str:
    return json.dumps(
        jsonable_encoder(value),
        separators=(",", ":"),
        sort_keys=True,
    )


def _deserialize(value: Optional[str]) -> Any:
    if value is None or value == "":
        return None
    try:
        return json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        CACHE_DESERIALIZE_FAILURES.inc()
        logger.warning("CACHE_DESERIALIZE_FAILURE", extra={"cache_key": None, "value_type": type(value).__name__})
        return None


# ---------------------------
# Sync Cache Functions
# ---------------------------

def cache_get_json_sync(key: str) -> Any:
    try:
        value = sync_redis.get(key)
    except Exception:
        CACHE_GET_FAILURES.inc()
        logger.warning("CACHE_GET_FAILURE", extra={"cache_key": key, "source": "sync"})
        return None

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
    try:
        sync_redis.set(
            key,
            _serialize(value),
            ex=ttl,
        )
    except Exception:
        CACHE_SET_FAILURES.inc()
        logger.warning("CACHE_SET_FAILURE", extra={"cache_key": key, "ttl": ttl, "source": "sync"})


def cache_delete_sync(key: str) -> None:
    try:
        sync_redis.delete(key)
    except Exception:
        CACHE_DELETE_FAILURES.inc()
        logger.warning("CACHE_DELETE_FAILURE", extra={"cache_key": key, "source": "sync"})


# ---------------------------
# Async Cache Functions
# ---------------------------

async def cache_get_json(key: str) -> Any:
    try:
        value = await async_redis.get(key)
    except Exception:
        CACHE_GET_FAILURES.inc()
        logger.warning("CACHE_GET_FAILURE", extra={"cache_key": key, "source": "async"})
        return None

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
    try:
        await async_redis.set(
            key,
            _serialize(value),
            ex=ttl,
        )
    except Exception:
        CACHE_SET_FAILURES.inc()
        logger.warning("CACHE_SET_FAILURE", extra={"cache_key": key, "ttl": ttl, "source": "async"})


async def cache_delete(key: str) -> None:
    try:
        await async_redis.delete(key)
    except Exception:
        CACHE_DELETE_FAILURES.inc()
        logger.warning("CACHE_DELETE_FAILURE", extra={"cache_key": key, "source": "async"})