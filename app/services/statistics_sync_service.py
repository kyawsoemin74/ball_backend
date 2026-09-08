import logging
from typing import Any, Dict

from sqlalchemy.ext.asyncio import AsyncSession

from app.providers.statistics_provider import StatisticsProvider
from app.repositories.statistics_repository import StatisticsRepository
from app.services.analytics_projection_service import AnalyticsProjectionService, log_projection_failure
from app.monitoring import observe_sync

logger = logging.getLogger(__name__)


class StatisticsSyncService:
    """Write/refresh orchestration for statistics data without owning read or cache logic."""

    def __init__(
        self,
        statistics_provider: StatisticsProvider | None = None,
        statistics_repository: StatisticsRepository | None = None,
        analytics_projection_service: AnalyticsProjectionService | None = None,
    ) -> None:
        self.statistics_provider = statistics_provider
        self.statistics_repository = statistics_repository or StatisticsRepository()
        self.analytics_projection_service = analytics_projection_service or AnalyticsProjectionService()

    @observe_sync("statistics")
    async def sync_match_statistics(self, db: AsyncSession, match_id: int) -> Dict[str, Any]:
        logger.info("STATISTICS_SYNC_START", extra={"match_id": match_id})

        result = await self.statistics_provider.get_match_statistics(match_id)
        if not result or "response" not in result:
            log_projection_failure("statistics", {"match_id": match_id}, "provider_failure")
            logger.warning("STATISTICS_SYNC_FAILED", extra={"match_id": match_id, "reason": "api_error"})
            return {"success": False, "message": "Statistics not found"}

        data = result.get("response")
        if not data:
            log_projection_failure("statistics", {"match_id": match_id}, "empty_provider_response")
            logger.warning("STATISTICS_SYNC_FAILED", extra={"match_id": match_id, "reason": "no_data"})
            return {"success": False, "message": "Statistics not found"}

        await self.statistics_repository.replace_match_statistics(db, match_id, data)
        await db.flush()
        projection = await self.analytics_projection_service.project_statistics(db, match_id, result)
        if not projection["success"]:
            raise ValueError(f"Statistics analytics projection rejected: {projection.get('reason', 'reconciliation failure')}")

        logger.info("STATISTICS_SYNC_COMPLETE", extra={"match_id": match_id, "count": len(data)})
        return {"success": True, "match_id": match_id, "data": data, "analytics": projection}
