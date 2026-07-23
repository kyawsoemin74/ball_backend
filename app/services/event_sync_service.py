import logging
from datetime import datetime, timezone
from typing import Any, Dict

from sqlalchemy.ext.asyncio import AsyncSession

from app.providers.event_provider import EventProvider
from app.repositories.event_repository import EventRepository

logger = logging.getLogger(__name__)


class EventSyncService:
    """Write/refresh orchestration for event data without owning read or cache logic."""

    def __init__(
        self,
        event_provider: EventProvider | None = None,
        event_repository: EventRepository | None = None,
    ) -> None:
        self.event_provider = event_provider
        self.event_repository = event_repository or EventRepository()

    async def refresh_match_events(self, db: AsyncSession, match_id: int) -> Dict[str, Any]:
        logger.info("FINAL_EVENT_SYNC_START", extra={"match_id": match_id})

        result = await self.event_provider.get_match_events(match_id)
        if not result or "response" not in result:
            logger.warning("EVENT_SYNC_FAILED", extra={"match_id": match_id, "reason": "api_error"})
            return {"success": False, "message": "API error"}

        api_events = result["response"]
        await self.event_repository.replace_match_events(db, match_id, api_events)
        await db.flush()

        logger.info("FINAL_EVENT_SYNC_COMPLETE", extra={"match_id": match_id, "count": len(api_events)})
        # Return events for caller to inspect; cache invalidation should occur
        # after the caller commits the transaction.
        return {"success": True, "count": len(api_events), "api_events": api_events}
