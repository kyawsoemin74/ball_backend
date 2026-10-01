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

    @staticmethod
    def _validate_response(result: Any) -> tuple[list[dict] | None, str | None]:
        if not isinstance(result, dict):
            return None, "malformed_provider_response"
        if result.get("errors"):
            return None, "provider_error"
        if "response" not in result:
            return None, "missing_response"

        data = result["response"]
        if not isinstance(data, list):
            return None, "malformed_response"
        if not data:
            return None, "empty_response"

        provider_team_ids = set()
        statistic_count = 0
        for entry in data:
            if not isinstance(entry, dict) or not isinstance(entry.get("team"), dict):
                return None, "invalid_statistics_structure"
            provider_team_id = entry["team"].get("id")
            statistics = entry.get("statistics")
            if provider_team_id is None or not isinstance(statistics, list):
                return None, "invalid_statistics_structure"
            provider_team_ids.add(str(provider_team_id))
            for statistic in statistics:
                if not isinstance(statistic, dict):
                    return None, "invalid_statistics_structure"
                name = statistic.get("type") or statistic.get("name")
                if not isinstance(name, str) or not name.strip() or "value" not in statistic:
                    return None, "invalid_statistics_structure"
                statistic_count += 1

        if len(provider_team_ids) < 2 or statistic_count == 0:
            return None, "invalid_statistics_structure"
        return data, None

    @observe_sync("statistics")
    async def sync_match_statistics(
        self,
        db: AsyncSession,
        match_id: int,
        *,
        provider_fixture_id: int | None = None,
    ) -> Dict[str, Any]:
        logger.info("STATISTICS_SYNC_START", extra={"match_id": match_id})

        provider_fixture_id = match_id if provider_fixture_id is None else provider_fixture_id
        result = await self.statistics_provider.get_match_statistics(provider_fixture_id)
        data, rejection_reason = self._validate_response(result)
        if rejection_reason:
            log_projection_failure("statistics", {"match_id": match_id}, rejection_reason)
            logger.warning("STATISTICS_SYNC_FAILED", extra={"match_id": match_id, "reason": rejection_reason})
            return {"success": False, "message": "Statistics not found", "reason": rejection_reason}

        projection = await self.analytics_projection_service.project_statistics(db, match_id, result)
        if not projection["success"]:
            raise ValueError(f"Statistics analytics projection rejected: {projection.get('reason', 'reconciliation failure')}")

        await self.statistics_repository.replace_match_statistics(db, match_id, data)
        await db.flush()

        logger.info("STATISTICS_SYNC_COMPLETE", extra={"match_id": match_id, "count": len(data)})
        return {"success": True, "match_id": match_id, "data": data, "analytics": projection}
