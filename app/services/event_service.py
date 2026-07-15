import logging
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache import make_cache_key
from app.core.config import settings
from app.models.match import Match
from app.models.match_event import MatchEvent
from app.providers.event_provider import EventProvider
from app.services.base.football_client import FootballAPIClient
from app.repositories.event_repository import EventRepository
from app.services.cache_service import CacheService
from app.services.event_sync_service import EventSyncService

logger = logging.getLogger(__name__)

FINISHED_STATUSES = {"FT", "AET", "PEN"}
LIVE_STATUSES = {"1H", "HT", "2H", "ET", "LIVE"}
LIVE_REFRESH_WINDOW = timedelta(minutes=10)


class EventService:
    def __init__(
        self,
        client: FootballAPIClient,
        cache_service: CacheService | None = None,
        event_provider: EventProvider | None = None,
        event_sync_service: EventSyncService | None = None,
        event_repository: EventRepository | None = None,
    ) -> None:
        self.client = client
        self.cache_service = cache_service or CacheService()
        self.event_provider = event_provider or EventProvider(self.client)
        self.event_repository = event_repository or EventRepository()
        self.event_sync_service = event_sync_service or EventSyncService(
            event_provider=self.event_provider,
            event_repository=self.event_repository,
        )

    async def get_match_events(self, match_id: int) -> dict:
        return await self.event_provider.get_match_events(match_id)

    @staticmethod
    def _serialize_db_events(events: List[MatchEvent]) -> List[Dict[str, Any]]:
        return [{
            "id": event.id,
            "match_id": event.match_id,
            "time_elapsed": event.time_elapsed,
            "time_extra": event.time_extra,
            "team_id": event.team_id,
            "team_name": event.team_name,
            "player_id": event.player_id,
            "player_name": event.player_name,
            "assist_id": event.assist_id,
            "assist_name": event.assist_name,
            "type": event.type,
            "detail": event.detail,
            "comments": event.comments,
        } for event in events]

    @staticmethod
    def _serialize_api_events(match_id: int, events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        payload = []
        for event in events:
            payload.append({
                "id": None,
                "match_id": match_id,
                "time_elapsed": event.get("time", {}).get("elapsed"),
                "time_extra": event.get("time", {}).get("extra"),
                "team_id": event.get("team", {}).get("id"),
                "team_name": event.get("team", {}).get("name"),
                "player_id": event.get("player", {}).get("id"),
                "player_name": event.get("player", {}).get("name"),
                "assist_id": event.get("assist", {}).get("id"),
                "assist_name": event.get("assist", {}).get("name"),
                "type": event.get("type"),
                "detail": event.get("detail"),
                "comments": event.get("comments"),
            })
        return payload

    async def _refresh_events_from_api(
        self,
        db: AsyncSession,
        match_id: int,
        cache_key: str,
        ttl: int,
        status: str,
        reason: str | None = None,
    ) -> List[Dict[str, Any]]:
        # Refresh-on-read is not allowed under the frozen architecture.
        # This method is intentionally removed from read-path responsibilities.
        # Keep signature for compatibility but do not perform refresh.
        return []

    async def sync_match_events(self, db: AsyncSession, match_id: int) -> dict:
        # Delegate sync orchestration to EventSyncService. Cache invalidation
        # and commit/rollback are the responsibility of the caller (scheduler/admin).
        return await self.event_sync_service.refresh_match_events(db, match_id)

    async def get_cached_match_events(self, db: AsyncSession, match_id: int) -> List[Dict[str, Any]]:
        cache_key = make_cache_key("match", match_id, "events")
        cached = await self.cache_service.get_json(cache_key)
        if cached is not None:
            return cached

        match = (await db.execute(select(Match).where(Match.match_id == match_id))).scalar_one_or_none()
        if not match:
            return []

        status = (match.status or "").upper()
        ttl = 120 if status in LIVE_STATUSES else settings.REDIS_TTL_STANDINGS
        db_events = await self.event_repository.get_by_match_id(db, match_id)

        if db_events:
            # Under frozen architecture read path must not refresh data.
            payload = self._serialize_db_events(db_events)
            await self.cache_service.set_json(cache_key, payload, ttl)
            return payload

        # No DB rows: do not trigger refresh from read path — return empty list
        return []
