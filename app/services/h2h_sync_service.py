import logging
from typing import TYPE_CHECKING

from app.providers.h2h_provider import H2HProvider
from app.repositories.h2h_repository import H2HRepository
from app.services.cache_service import CacheService
from app.services.analytics_projection_service import AnalyticsProjectionService, log_projection_failure
from app.repositories.team_repository import TeamRepository
from app.monitoring import observe_sync
from app.models.match import Match
from sqlalchemy import select

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
    async def refresh_h2h(self, db, local_match_id: int) -> dict:
        try:
            local_match_id = int(local_match_id)
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid local_match_id for H2H refresh") from exc
        if local_match_id <= 0:
            raise ValueError("invalid local_match_id for H2H refresh")

        match_result = await db.execute(
            select(Match).where(Match.local_match_id == local_match_id)
        )
        match = match_result.scalar_one_or_none()
        if match is None:
            raise ValueError("match not found for H2H refresh")
        if str(getattr(match, "provider", "")).casefold() != "api-football":
            raise ValueError("unsupported provider for H2H refresh")
        try:
            provider_fixture_id = int(getattr(match, "provider_fixture_id", 0))
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid provider fixture identity for H2H refresh") from exc
        if provider_fixture_id <= 0:
            raise ValueError("invalid provider fixture identity for H2H refresh")

        local_team_ids = [getattr(match, "home_team_id", None), getattr(match, "away_team_id", None)]
        if any(team_id is None for team_id in local_team_ids) or int(local_team_ids[0]) == int(local_team_ids[1]):
            raise ValueError("H2H requires a match with two distinct teams")

        local_teams = [
            await self.team_repository.get_by_id(db, int(team_id))
            for team_id in local_team_ids
        ]
        if any(team is None for team in local_teams):
            raise ValueError("missing Team identity for H2H refresh")
        if any(str(getattr(team, "provider", "api-football")).casefold() != "api-football" for team in local_teams):
            raise ValueError("unsupported Team provider for H2H refresh")
        if any(getattr(team, "provider_id", None) is None for team in local_teams):
            raise ValueError("unresolved Team identity for H2H refresh")

        raw_provider_ids = [team.provider_id for team in local_teams]
        if any(
            isinstance(provider_id, bool)
            or not isinstance(provider_id, (int, str))
            or (isinstance(provider_id, str) and not provider_id.strip().isdigit())
            for provider_id in raw_provider_ids
        ):
            raise ValueError("invalid provider team identity for H2H refresh")
        try:
            provider_ids = [int(provider_id) for provider_id in raw_provider_ids]
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid provider team identity for H2H refresh") from exc
        if any(provider_id <= 0 for provider_id in provider_ids) or provider_ids[0] == provider_ids[1]:
            raise ValueError("invalid provider team identity for H2H refresh")

        provider_h2h_key = f"{provider_ids[0]}-{provider_ids[1]}"
        api_res = await self.h2h_provider.get_h2h_by_team_ids(provider_ids[0], provider_ids[1])
        if not api_res or "response" not in api_res:
            log_projection_failure("h2h", {"local_match_id": local_match_id, "provider_h2h_key": provider_h2h_key}, "provider_failure")
            return {"error": "API error"}

        h2h_data = api_res["response"]
        if not isinstance(h2h_data, list):
            raise ValueError("Invalid H2H provider response")

        local_ids = sorted(int(team.team_id) for team in local_teams)
        canonical_source_key = f"{local_ids[0]}-{local_ids[1]}"
        await self.h2h_repository.upsert_one(db, canonical_source_key, h2h_data)
        await db.flush()

        projection = await self.analytics_projection_service.project_h2h(
            db, provider_ids[0], provider_ids[1], h2h_data
        )
        if not projection["success"]:
            raise ValueError(f"H2H analytics projection rejected: {projection.get('reason', 'reconciliation failure')}")

        return {"source": "api", "data": h2h_data, "cached": False, "updated": True, "analytics": projection}
