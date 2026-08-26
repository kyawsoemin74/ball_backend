import logging
from dataclasses import dataclass
from collections.abc import Awaitable, Callable

from sqlalchemy import text

logger = logging.getLogger(__name__)


_RESOURCE_PREFIX = "fover:sync"
_ADVISORY_LOCK_SQL = "SELECT pg_try_advisory_lock(hashtextextended(:lock_identity, 0))"
_ADVISORY_UNLOCK_SQL = "SELECT pg_advisory_unlock(hashtextextended(:lock_identity, 0))"


@dataclass(frozen=True)
class ResourceLockHandle:
    db: object
    lock_key: str



def build_resource_identity(resource_type: str, resource_identity: object) -> str:
    """Build the normalized, namespaced identity used by every sync actor."""
    normalized_type = str(resource_type).strip().casefold()
    normalized_identity = str(resource_identity).strip().casefold()
    if not normalized_type or not normalized_identity:
        raise ValueError("resource type and identity are required")
    return f"{_RESOURCE_PREFIX}:{normalized_type}:{normalized_identity}"


def resource_lock_identity(resource_type: str, resource_identity: object) -> str:
    """Backward-compatible alias for canonical resource identity generation."""
    return build_resource_identity(resource_type, resource_identity)


async def acquire_resource_lock(
    db,
    resource_type: str,
    resource_identity: object,
) -> ResourceLockHandle | None:
    """Acquire a non-blocking PostgreSQL advisory lock or return None."""
    lock_key = build_resource_identity(resource_type, resource_identity)
    if not hasattr(db, "execute"):
        return ResourceLockHandle(db=None, lock_key=lock_key)
    try:
        logger.info("RESOURCE_LOCK_ACQUIRE_ATTEMPT lock_identity=%s", lock_key)
        result = await db.execute(
            text(_ADVISORY_LOCK_SQL),
            {"lock_identity": lock_key},
        )
        if not bool(result.scalar_one()):
            logger.info("RESOURCE_LOCK_CONFLICT lock_identity=%s", lock_key)
            return None
        logger.info("RESOURCE_LOCK_ACQUIRED lock_identity=%s", lock_key)
        return ResourceLockHandle(db=db, lock_key=lock_key)
    except Exception:
        logger.exception("RESOURCE_LOCK_ACQUIRE_FAILED lock_identity=%s", lock_key)
        return None


async def release_resource_lock(handle: ResourceLockHandle | None) -> None:
    if handle is None:
        return
    if handle.db is None:
        return
    try:
        await handle.db.execute(
            text(_ADVISORY_UNLOCK_SQL),
            {"lock_identity": handle.lock_key},
        )
        logger.info("RESOURCE_LOCK_RELEASED lock_identity=%s", handle.lock_key)
    except Exception:
        logger.exception("RESOURCE_LOCK_RELEASE_FAILED lock_identity=%s", handle.lock_key)


async def run_with_resource_lock(
    db,
    resource_type: str,
    resource_identity: object,
    operation: Callable[[], Awaitable[object]],
) -> tuple[bool, object | None]:
    """Run an operation while holding one shared resource lock."""
    handle = await acquire_resource_lock(db, resource_type, resource_identity)
    if handle is None:
        return False, None
    try:
        return True, await operation()
    finally:
        await release_resource_lock(handle)
