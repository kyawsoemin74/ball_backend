import logging
from datetime import datetime, timezone
from typing import Any, Callable, Dict

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.providers.lineup_provider import LineupProvider
from app.repositories.lineup_repository import LineupRepository
from app.services.analytics_projection_service import AnalyticsProjectionService, log_projection_failure

logger = logging.getLogger(__name__)

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


class LineupSyncService:
    """Write/refresh orchestration for lineup data without owning read or cache logic."""

    def __init__(
        self,
        lineup_provider: LineupProvider | None = None,
        lineup_repository: LineupRepository | None = None,
        analytics_projection_service: AnalyticsProjectionService | None = None,
    ) -> None:
        self.lineup_provider = lineup_provider
        self.lineup_repository = lineup_repository or LineupRepository()
        self.analytics_projection_service = analytics_projection_service or AnalyticsProjectionService()

    async def sync_lineup(
        self,
        db: AsyncSession,
        match_id: int,
        *,
        validate_lineup: Callable[[Any], bool],
        cache_service: Any | None = None,
        cache_key: str | None = None,
        allow_terminal_status: bool = False,
    ) -> Dict[str, Any]:
        logger.info("LINEUP_SYNC_START", extra={"match_id": match_id})

        try:
            match = await self.lineup_repository.get_match_status(db, match_id)
            status = (match.status or "").upper() if match and match.status else None
            logger.debug("LINEUP_STATUS_GATE", extra={"match_id": match_id, "status": status})

            if status in LINEUP_SYNC_BLOCKED_STATUSES and not allow_terminal_status:
                metrics = {
                    "success": True,
                    "match_id": match_id,
                    "skipped": True,
                    "reason": "status_blocked",
                    "status": status,
                }
                logger.debug("LINEUP_SYNC_SKIPPED_STATUS", extra={"match_id": match_id, "status": status})
                logger.info("LINEUP_SYNC_COMPLETE", extra=_safe_lineup_log_extra(metrics))
                return metrics

            api_res = await self.lineup_provider.get_match_lineup(match_id)
            lineup_data = api_res.get("response") if isinstance(api_res, dict) else None
            logger.debug(
                "LINEUP_SYNC_FETCHED",
                extra={"match_id": match_id, "has_response": lineup_data is not None},
            )

            if not validate_lineup(lineup_data):
                log_projection_failure("lineup", {"match_id": match_id}, "invalid_provider_response")
                metrics = {"success": False, "match_id": match_id, "reason": "lineup_not_available"}
                logger.warning("LINEUP_SYNC_FAILED", extra=_safe_lineup_log_extra(metrics))
                logger.info("LINEUP_SYNC_COMPLETE", extra=_safe_lineup_log_extra(metrics))
                return metrics

            existing = await self.lineup_repository.get_by_match_id(db, match_id)

            if existing:
                await self.lineup_repository.update_one(db, existing, lineup_data)
                await db.flush()
                try:
                    projection = await self.analytics_projection_service.project_lineup(db, match_id, lineup_data)
                except AttributeError as exc:
                    if not self.analytics_projection_service.unavailable_for_fake_db(exc):
                        raise
                    projection = None
                if projection is not None and not projection["success"]:
                    raise ValueError(f"Lineup analytics projection rejected: {projection.get('reason', 'reconciliation failure')}")
                metrics = {"success": True, "match_id": match_id, "created": False, "updated": True}
                if projection is not None:
                    metrics["analytics"] = projection
                logger.info("LINEUP_SYNC_UPDATED", extra=_safe_lineup_log_extra(metrics))
                logger.info("LINEUP_SYNC_COMPLETE", extra=_safe_lineup_log_extra(metrics))
                return metrics

            await self.lineup_repository.create_one(db, match_id, lineup_data)
            try:
                await db.flush()
            except IntegrityError:
                existing_after_race = await self.lineup_repository.get_by_match_id(db, match_id)
                if not existing_after_race:
                    metrics = {"success": False, "match_id": match_id, "reason": "lineup_sync_failed"}
                    logger.warning("LINEUP_SYNC_FAILED", extra=_safe_lineup_log_extra(metrics))
                    logger.info("LINEUP_SYNC_COMPLETE", extra=_safe_lineup_log_extra(metrics))
                    return metrics

                await self.lineup_repository.update_one(db, existing_after_race, lineup_data)
                await db.flush()
                try:
                    projection = await self.analytics_projection_service.project_lineup(db, match_id, lineup_data)
                except AttributeError as exc:
                    if not self.analytics_projection_service.unavailable_for_fake_db(exc):
                        raise
                    projection = None
                if projection is not None and not projection["success"]:
                    raise ValueError(f"Lineup analytics projection rejected: {projection.get('reason', 'reconciliation failure')}")
                metrics = {"success": True, "match_id": match_id, "created": False, "updated": True}
                if projection is not None:
                    metrics["analytics"] = projection
                logger.debug("LINEUP_SYNC_UPDATED", extra=_safe_lineup_log_extra(metrics))
                logger.info("LINEUP_SYNC_COMPLETE", extra=_safe_lineup_log_extra(metrics))
                return metrics

            try:
                projection = await self.analytics_projection_service.project_lineup(db, match_id, lineup_data)
            except AttributeError as exc:
                if not self.analytics_projection_service.unavailable_for_fake_db(exc):
                    raise
                projection = None
            if projection is not None and not projection["success"]:
                raise ValueError(f"Lineup analytics projection rejected: {projection.get('reason', 'reconciliation failure')}")
            metrics = {"success": True, "match_id": match_id, "created": True, "updated": False}
            if projection is not None:
                metrics["analytics"] = projection
            logger.info("LINEUP_SYNC_CREATED", extra=_safe_lineup_log_extra(metrics))
            logger.info("LINEUP_SYNC_COMPLETE", extra=_safe_lineup_log_extra(metrics))
            return metrics
        except Exception as exc:
            metrics = {"success": False, "match_id": match_id, "reason": "lineup_sync_failed"}
            logger.exception("LINEUP_SYNC_FAILED", extra=_safe_lineup_log_extra({**metrics, "error": str(exc)}))
            logger.info("LINEUP_SYNC_COMPLETE", extra=_safe_lineup_log_extra(metrics))
            return metrics
