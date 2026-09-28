import logging

from sqlalchemy import exists, select

from app.cache import make_cache_key
from app.db import async_session
from app.models.analytics import AnalyticsMatchLineup
from app.models.match_lineup import MatchLineup
from app.models.match_lineup_finalization import MatchLineupFinalization
from app.services.analytics_projection_service import AnalyticsProjectionService, log_projection_transaction
from app.services.cache_service import CacheService
from app.services.resource_lock import run_with_resource_lock

logger = logging.getLogger(__name__)


class LineupAnalyticsRepairService:
    """One-time repair orchestration over canonical persisted lineup payloads."""

    def __init__(
        self,
        projection_service: AnalyticsProjectionService | None = None,
        cache_service: CacheService | None = None,
    ) -> None:
        self.projection_service = projection_service or AnalyticsProjectionService()
        self.cache_service = cache_service or CacheService()

    async def _candidate_match_ids(self, match_ids: list[int] | None) -> list[int]:
        async with async_session() as db:
            query = select(MatchLineup.local_match_id).where(
                ~exists(
                    select(1).where(AnalyticsMatchLineup.match_id == MatchLineup.local_match_id)
                )
            )
            query = query.join(
                MatchLineupFinalization,
                MatchLineupFinalization.match_id == MatchLineup.local_match_id,
            ).where(MatchLineupFinalization.status == "SUCCESS")
            if match_ids is not None:
                query = query.where(MatchLineup.local_match_id.in_(match_ids))
            result = await db.execute(query.order_by(MatchLineup.local_match_id.asc()))
            return [int(match_id) for match_id in result.scalars().all()]

    async def repair_missing_projections(self, match_ids: list[int] | None = None) -> dict[str, int]:
        metrics = {"selected": 0, "projected": 0, "skipped": 0, "failed": 0}
        candidate_ids = await self._candidate_match_ids(match_ids)
        metrics["selected"] = len(candidate_ids)

        for match_id in candidate_ids:
            try:
                async with async_session() as db:
                    async def project() -> dict:
                        record = (
                            await db.execute(
                                select(MatchLineup).where(MatchLineup.local_match_id == match_id)
                            )
                        ).scalar_one_or_none()
                        if record is None:
                            return {"success": False, "reason": "lineup_missing"}
                        result = await self.projection_service.project_lineup(db, match_id, record.data)
                        if not result.get("success"):
                            return result
                        await db.flush()
                        return result

                    locked, result = await run_with_resource_lock(db, "lineup", match_id, project)
                    if not locked:
                        metrics["skipped"] += 1
                        logger.info("LINEUP_ANALYTICS_REPAIR_SKIPPED match_id=%s reason=lock_conflict", match_id)
                        continue

                    if not isinstance(result, dict) or not result.get("success"):
                        await db.rollback()
                        metrics["failed"] += 1
                        logger.warning(
                            "LINEUP_ANALYTICS_REPAIR_FAILED match_id=%s reason=%s",
                            match_id,
                            (result or {}).get("reason", "projection_failed") if isinstance(result, dict) else "projection_failed",
                        )
                        continue

                    await db.commit()
                    log_projection_transaction(result, "committed")
                    try:
                        await self.cache_service.delete(make_cache_key("lineup", match_id))
                    except Exception:
                        logger.exception("LINEUP_ANALYTICS_REPAIR_CACHE_INVALIDATION_FAILED match_id=%s", match_id)
                    metrics["projected"] += 1
                    logger.info(
                        "LINEUP_ANALYTICS_REPAIR_PROJECTED match_id=%s analytics_count=%s",
                        match_id,
                        result.get("analytics_count"),
                    )
            except Exception as exc:
                metrics["failed"] += 1
                logger.exception(
                    "LINEUP_ANALYTICS_REPAIR_FAILED match_id=%s error_type=%s",
                    match_id,
                    exc.__class__.__name__,
                )

        return metrics