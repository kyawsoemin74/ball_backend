import logging
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache import make_cache_key
from app.core.config import settings
from app.models.match import Match
from app.models.match_event import MatchEvent
from app.services.base.football_client import FootballAPIClient
from app.services.cache_service import CacheService

logger = logging.getLogger(__name__)

FINISHED_STATUSES = {"FT", "AET", "PEN"}
LIVE_STATUSES = {"1H", "HT", "2H", "ET", "LIVE"}
LIVE_REFRESH_WINDOW = timedelta(minutes=10)


class EventService:
    def __init__(self, client: FootballAPIClient, cache_service: CacheService | None = None) -> None:
        self.client = client
        self.cache_service = cache_service or CacheService()

    async def get_match_events(self, match_id: int) -> dict:
        return await self.client.get("/fixtures/events", params={"fixture": match_id})

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

    async def _replace_events_from_api(
        self,
        db: AsyncSession,
        match_id: int,
        api_events: List[Dict[str, Any]],
        refresh_ts: datetime,
    ) -> None:
        await db.execute(delete(MatchEvent).where(MatchEvent.match_id == match_id))
        for event in api_events:
            db.add(MatchEvent(
                match_id=match_id,
                time_elapsed=event.get("time", {}).get("elapsed"),
                time_extra=event.get("time", {}).get("extra"),
                team_id=event.get("team", {}).get("id"),
                team_name=event.get("team", {}).get("name"),
                player_id=event.get("player", {}).get("id"),
                player_name=event.get("player", {}).get("name"),
                assist_id=event.get("assist", {}).get("id"),
                assist_name=event.get("assist", {}).get("name"),
                type=event.get("type"),
                detail=event.get("detail"),
                comments=event.get("comments"),
                updated_at=refresh_ts,
            ))
        await db.flush()

    async def sync_match_events(self, db: AsyncSession, match_id: int) -> dict:
        result = await self.get_match_events(match_id)
        if not result or "response" not in result:
            return {"success": False, "message": "API error"}

        events_data = result["response"]
        await self._replace_events_from_api(db, match_id, events_data, datetime.now(timezone.utc))
        await db.commit()
        await self.cache_service.delete(make_cache_key("match", match_id, "events"))
        return {"success": True, "count": len(events_data)}

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
        db_events = (
            await db.execute(
                select(MatchEvent)
                .where(MatchEvent.match_id == match_id)
                .order_by(MatchEvent.time_elapsed, MatchEvent.time_extra)
            )
        ).scalars().all()

        if status in FINISHED_STATUSES:
            if db_events:
                logger.info("EVENT_FT_DB_ONLY", extra={"match_id": match_id, "status": status})
                payload = self._serialize_db_events(db_events)
                await self.cache_service.set_json(cache_key, payload, settings.REDIS_TTL_STANDINGS)
                return payload

            logger.info("EVENT_FT_RECOVERY", extra={"match_id": match_id, "status": status})
            result = await self.get_match_events(match_id)
            if not result or "response" not in result:
                return []
            api_events = result["response"]
            refresh_ts = datetime.now(timezone.utc)
            await self._replace_events_from_api(db, match_id, api_events, refresh_ts)
            await db.commit()
            logger.info("EVENT_API_REFRESH", extra={"match_id": match_id, "status": status})
            await self.cache_service.delete(cache_key)
            payload = self._serialize_api_events(match_id, api_events)
            await self.cache_service.set_json(cache_key, payload, settings.REDIS_TTL_STANDINGS)
            return payload

        if status in LIVE_STATUSES:
            if not db_events:
                result = await self.get_match_events(match_id)
                if not result or "response" not in result:
                    return []
                api_events = result["response"]
                refresh_ts = datetime.now(timezone.utc)
                await self._replace_events_from_api(db, match_id, api_events, refresh_ts)
                await db.commit()
                logger.info("EVENT_API_REFRESH", extra={"match_id": match_id, "status": status, "reason": "db_empty"})
                await self.cache_service.delete(cache_key)
                payload = self._serialize_api_events(match_id, api_events)
                await self.cache_service.set_json(cache_key, payload, ttl)
                return payload

            latest_refresh = max(
                (event.updated_at or event.created_at for event in db_events if (event.updated_at or event.created_at)),
                default=None,
            )
            now_utc = datetime.now(timezone.utc)
            if latest_refresh and (now_utc - latest_refresh) < LIVE_REFRESH_WINDOW:
                logger.info("EVENT_DB_FRESH", extra={"match_id": match_id, "status": status})
                payload = self._serialize_db_events(db_events)
                await self.cache_service.set_json(cache_key, payload, ttl)
                return payload

            logger.info("EVENT_DB_STALE", extra={"match_id": match_id, "status": status})
            result = await self.get_match_events(match_id)
            if not result or "response" not in result:
                return []
            api_events = result["response"]
            refresh_ts = datetime.now(timezone.utc)
            await self._replace_events_from_api(db, match_id, api_events, refresh_ts)
            await db.commit()
            logger.info("EVENT_API_REFRESH", extra={"match_id": match_id, "status": status})
            await self.cache_service.delete(cache_key)
            payload = self._serialize_api_events(match_id, api_events)
            await self.cache_service.set_json(cache_key, payload, ttl)
            return payload

        result = await self.get_match_events(match_id)
        if not result or "response" not in result:
            return []

        api_events = result["response"]
        refresh_ts = datetime.now(timezone.utc)
        await self._replace_events_from_api(db, match_id, api_events, refresh_ts)
        await db.commit()
        logger.info("EVENT_API_REFRESH", extra={"match_id": match_id, "status": status, "reason": "default"})
        await self.cache_service.delete(cache_key)
        payload = self._serialize_api_events(match_id, api_events)
        await self.cache_service.set_json(cache_key, payload, ttl)
        return payload
