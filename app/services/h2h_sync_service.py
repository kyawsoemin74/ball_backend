import logging
from typing import TYPE_CHECKING

from app.providers.h2h_provider import H2HProvider
from app.repositories.h2h_repository import H2HRepository
from app.services.cache_service import CacheService
from app.services.analytics_projection_service import AnalyticsProjectionService, log_projection_failure
from app.repositories.team_repository import TeamRepository
from app.monitoring import observe_sync

if TYPE_CHECKING:
    from app.services.h2h_service import H2HService

logger = logging.getLogger(__name__)


class H2HSyncService:
    """Write/refresh orchestration for H2H data without owning read or transport logic."""

    def __init__(
        self,
        h2h_service: "H2HService | None",
        cache_service: CacheService | None = None,
        h2h_provider: H2HProvider | None = None,
        h2h_repository: H2HRepository | None = None,
        analytics_projection_service: AnalyticsProjectionService | None = None,
        team_repository: TeamRepository | None = None,
    ) -> None:
        self.h2h_service = h2h_service
        self.cache_service = cache_service or CacheService()
        self.h2h_provider = h2h_provider
        self.h2h_repository = h2h_repository or H2HRepository()
        self.analytics_projection_service = analytics_projection_service or AnalyticsProjectionService()
        self.team_repository = team_repository or TeamRepository()

    @observe_sync("h2h")
    async def refresh_h2h(self, db, h2h_key: str) -> dict:
        api_res = await self.h2h_provider.get_h2h_by_key(h2h_key)
        if not api_res or "response" not in api_res:
            log_projection_failure("h2h", {"provider_h2h_key": h2h_key}, "provider_failure")
            return {"error": "API error"}

        h2h_data = api_res["response"]
        if not isinstance(h2h_data, list):
            raise ValueError("Invalid H2H provider response")

        provider_ids = [int(value) for value in h2h_key.split("-")]
        provider_teams = [
            await self.team_repository.find_by_provider_identity(db, "api-football", provider_id)
            for provider_id in provider_ids
        ]
        if any(team is None for team in provider_teams):
            raise ValueError("Unresolved provider Team identity in H2H")

        local_ids = sorted(int(team.team_id) for team in provider_teams)
        canonical_source_key = f"{local_ids[0]}-{local_ids[1]}"
        await self.h2h_repository.upsert_one(db, canonical_source_key, h2h_data)
        await db.flush()

        projection = await self.analytics_projection_service.project_h2h(
            db, provider_ids[0], provider_ids[1], h2h_data
        )
        if not projection["success"]:
            raise ValueError(f"H2H analytics projection rejected: {projection.get('reason', 'reconciliation failure')}")

        return {"source": "api", "data": h2h_data, "cached": False, "updated": True, "analytics": projection}
