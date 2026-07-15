import logging
from typing import Any, Dict

from sqlalchemy.ext.asyncio import AsyncSession

from app.providers.statistics_provider import StatisticsProvider
from app.repositories.statistics_repository import StatisticsRepository

logger = logging.getLogger(__name__)


class StatisticsSyncService:
    """Write/refresh orchestration for statistics data without owning read or cache logic."""

    def __init__(
        self,
        statistics_provider: StatisticsProvider | None = None,
        statistics_repository: StatisticsRepository | None = None,
    ) -> None:
        self.statistics_provider = statistics_provider
        self.statistics_repository = statistics_repository or StatisticsRepository()

    async def sync_match_statistics(self, db: AsyncSession, match_id: int) -> Dict[str, Any]:
        logger.info("STATISTICS_SYNC_START", extra={"match_id": match_id})

        result = await self.statistics_provider.get_match_statistics(match_id)
        if not result or "response" not in result:
            logger.warning("STATISTICS_SYNC_FAILED", extra={"match_id": match_id, "reason": "api_error"})
            return {"success": False, "message": "Statistics not found"}

        data = result.get("response")
        if not data:
            logger.warning("STATISTICS_SYNC_FAILED", extra={"match_id": match_id, "reason": "no_data"})
            return {"success": False, "message": "Statistics not found"}

        await self.statistics_repository.replace_match_statistics(db, match_id, data)
        await db.flush()

        logger.info("STATISTICS_SYNC_COMPLETE", extra={"match_id": match_id, "count": len(data)})
        return {"success": True, "match_id": match_id, "data": data}
