import logging
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache import make_cache_key
from app.core.config import settings
from app.models.match import Match
from app.models.match_lineup import MatchLineup
from app.services.base.football_client import FootballAPIClient
from app.services.cache_service import CacheService

logger = logging.getLogger(__name__)

LINEUP_SYNC_ALLOWED_STATUSES = {"NS", "1H", "HT", "2H", "LIVE"}
LINEUP_SYNC_BLOCKED_STATUSES = {"FT", "AET", "PEN", "PST", "CANC", "ABD", "AWD", "WO"}

_RESERVED_LOG_EXTRA_RENAMES = {
    "created": "lineup_created",
    "updated": "lineup_updated",
    "message": "lineup_message",
    "filename": "lineup_filename",
    "module": "lineup_module",
    "name": "lineup_name",
    "levelname": "lineup_levelname",
    "pathname": "lineup_pathname",
    "lineno": "lineup_lineno",
    "process": "lineup_process",
    "thread": "lineup_thread",
}


def _safe_lineup_log_extra(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {
        _RESERVED_LOG_EXTRA_RENAMES.get(key, key): value
        for key, value in payload.items()
    }


def make_lineup_cache_key(match_id: int) -> str:
    return make_cache_key("lineup", match_id)


class LineupService:
    def __init__(self, client: FootballAPIClient, cache_service: object | None = None) -> None:
        self.client = client
        self.cache_service = cache_service or CacheService()

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
        return await self.client.get("/fixtures/lineups", params={"fixture": match_id})

    async def sync_lineup(self, db: AsyncSession, match_id: int) -> Dict[str, Any]:
        logger.info("LINEUP_SYNC_START", extra={"match_id": match_id})

        try:
            match = (await db.execute(select(Match).where(Match.match_id == match_id))).scalar_one_or_none()
            status = (match.status or "").upper() if match and match.status else None
            logger.info("LINEUP_STATUS_GATE", extra={"match_id": match_id, "status": status})

            if status in LINEUP_SYNC_BLOCKED_STATUSES:
                metrics = {
                    "success": True,
                    "match_id": match_id,
                    "skipped": True,
                    "reason": "status_blocked",
                    "status": status,
                }
                logger.info("LINEUP_SYNC_SKIPPED_STATUS", extra={"match_id": match_id, "status": status})
                logger.info("LINEUP_SYNC_COMPLETE", extra=_safe_lineup_log_extra(metrics))
                return metrics

            api_res = await self.get_match_lineup(match_id)
            lineup_data = api_res.get("response") if isinstance(api_res, dict) else None
            logger.info(
                "LINEUP_SYNC_FETCHED",
                extra={"match_id": match_id, "has_response": lineup_data is not None},
            )

            if not self._is_valid_lineup_response(lineup_data):
                metrics = {"success": False, "match_id": match_id, "reason": "lineup_not_available"}
                logger.warning("LINEUP_SYNC_FAILED", extra=_safe_lineup_log_extra(metrics))
                logger.info("LINEUP_SYNC_COMPLETE", extra=_safe_lineup_log_extra(metrics))
                return metrics

            existing = (await db.execute(select(MatchLineup).where(MatchLineup.match_id == match_id))).scalar_one_or_none()

            if existing:
                existing.data = lineup_data
                existing.updated_at = datetime.now(timezone.utc)
                await db.flush()
                await db.commit()
                cache_key = make_lineup_cache_key(match_id)
                await self.cache_service.delete(cache_key)
                logger.info("LINEUP_CACHE_DELETE", extra={"match_id": match_id})
                metrics = {"success": True, "match_id": match_id, "created": False, "updated": True}
                logger.info("LINEUP_SYNC_UPDATED", extra=_safe_lineup_log_extra(metrics))
                logger.info("LINEUP_SYNC_COMPLETE", extra=_safe_lineup_log_extra(metrics))
                return metrics

            db.add(MatchLineup(match_id=match_id, data=lineup_data))
            await db.flush()
            try:
                await db.commit()
            except IntegrityError:
                await db.rollback()
                existing_after_race = (
                    await db.execute(select(MatchLineup).where(MatchLineup.match_id == match_id))
                ).scalar_one_or_none()
                if not existing_after_race:
                    metrics = {"success": False, "match_id": match_id, "reason": "lineup_sync_failed"}
                    logger.warning("LINEUP_SYNC_FAILED", extra=_safe_lineup_log_extra(metrics))
                    logger.info("LINEUP_SYNC_COMPLETE", extra=_safe_lineup_log_extra(metrics))
                    return metrics

                existing_after_race.data = lineup_data
                existing_after_race.updated_at = datetime.now(timezone.utc)
                await db.flush()
                await db.commit()
                cache_key = make_lineup_cache_key(match_id)
                await self.cache_service.delete(cache_key)
                logger.info("LINEUP_CACHE_DELETE", extra={"match_id": match_id})
                metrics = {"success": True, "match_id": match_id, "created": False, "updated": True}
                logger.info("LINEUP_SYNC_UPDATED", extra=_safe_lineup_log_extra(metrics))
                logger.info("LINEUP_SYNC_COMPLETE", extra=_safe_lineup_log_extra(metrics))
                return metrics

            cache_key = make_lineup_cache_key(match_id)
            await self.cache_service.delete(cache_key)
            logger.info("LINEUP_CACHE_DELETE", extra={"match_id": match_id})
            metrics = {"success": True, "match_id": match_id, "created": True, "updated": False}
            logger.info("LINEUP_SYNC_CREATED", extra=_safe_lineup_log_extra(metrics))
            logger.info("LINEUP_SYNC_COMPLETE", extra=_safe_lineup_log_extra(metrics))
            return metrics
        except Exception as exc:
            await db.rollback()
            metrics = {"success": False, "match_id": match_id, "reason": "lineup_sync_failed"}
            logger.exception("LINEUP_SYNC_FAILED", extra=_safe_lineup_log_extra({**metrics, "error": str(exc)}))
            logger.info("LINEUP_SYNC_COMPLETE", extra=_safe_lineup_log_extra(metrics))
            return metrics

    async def get_cached_match_lineup(self, db: AsyncSession, match_id: int) -> Optional[List[Dict[str, Any]]]:
        cache_key = make_lineup_cache_key(match_id)
        cached = await self.cache_service.get_json(cache_key)
        if cached is not None:
            logger.info("LINEUP_CACHE_HIT", extra={"match_id": match_id})
            return cached

        logger.info("LINEUP_CACHE_MISS", extra={"match_id": match_id})
        db_record = (await db.execute(select(MatchLineup).where(MatchLineup.match_id == match_id))).scalar_one_or_none()
        if db_record:
            await self.cache_service.set_json(cache_key, db_record.data, settings.REDIS_TTL_LINEUP)
            logger.info("LINEUP_CACHE_SET", extra={"match_id": match_id})
            return db_record.data

        sync_result = await self.sync_lineup(db=db, match_id=match_id)
        if not sync_result.get("success"):
            return None

        db_record = (await db.execute(select(MatchLineup).where(MatchLineup.match_id == match_id))).scalar_one_or_none()
        if not db_record:
            return None

        await self.cache_service.set_json(cache_key, db_record.data, settings.REDIS_TTL_LINEUP)
        logger.info("LINEUP_CACHE_SET", extra={"match_id": match_id})
        return db_record.data
