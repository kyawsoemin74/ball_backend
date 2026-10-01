import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from app.providers.odds_provider import OddsProvider
from app.repositories.odds_repository import OddsRepository
from app.services.cache_service import CacheService
from app.services.analytics_projection_service import AnalyticsProjectionService, log_projection_failure
from app.services.base.football_client import FootballAPIResponse
from app.monitoring import observe_sync

if TYPE_CHECKING:
    from app.services.odds_service import OddsService

logger = logging.getLogger(__name__)

COMMIT_UNKNOWN = "COMMIT_UNKNOWN"
COMMIT_CONFIRMED = "COMMIT_CONFIRMED"
CACHE_INVALIDATION_FAILED = "CACHE_INVALIDATION_FAILED"
SUCCESS = "SUCCESS"


class OddsTransactionFailure(RuntimeError):
    """Failure that must be rolled back by the existing outer session owner."""


class OddsSyncService:
    """Write/refresh orchestration for odds data without owning read or transport logic."""

    def __init__(
        self,
        odds_service: "OddsService",
        cache_service: CacheService | None = None,
        odds_provider: OddsProvider | None = None,
        odds_repository: OddsRepository | None = None,
        analytics_projection_service: AnalyticsProjectionService | None = None,
    ) -> None:
        self.odds_service = odds_service
        self.cache_service = cache_service or CacheService()
        self.odds_provider = odds_provider or OddsProvider(self.odds_service.client)
        self.odds_repository = odds_repository or OddsRepository()
        self.analytics_projection_service = analytics_projection_service or AnalyticsProjectionService()

    @observe_sync("odds")
    async def refresh_odds(
        self,
        db,
        fixture_id: int,
        cache_key: str,
        pre_match_ttl: int,
        *,
        local_match_id: int | None = None,
    ) -> dict:
        local_match_id = fixture_id if local_match_id is None else local_match_id
        result = await self.odds_provider.get_match_odds(fixture_id)
        if isinstance(result, FootballAPIResponse):
            if result.exception_type or (result.status_code is None and result.payload is None):
                log_projection_failure("odds", {"match_id": fixture_id}, "provider_request_failure")
                return {"error": "API error", "reason": "provider_request_failure"}
            payload = result.payload
            if (
                (result.status_code is not None and result.status_code >= 400)
                or result.error_code
                or result.error_message
                or (isinstance(payload, dict) and payload.get("errors"))
            ):
                log_projection_failure("odds", {"match_id": fixture_id}, "provider_api_error")
                return {"error": "API error", "reason": "provider_api_error"}
            result = payload

        if not isinstance(result, dict) or "response" not in result:
            log_projection_failure("odds", {"match_id": fixture_id}, "provider_request_failure")
            return {"error": "API error", "reason": "provider_request_failure"}

        responses = result.get("response", [])
        if not responses:
            return {"odds": [], "source": "api", "cached": False, "match_started": False, "reason": "no_data"}

        odds_to_upsert = []
        one_xbet_missing = True
        for item in responses:
            if item.get("fixture", {}).get("id") != fixture_id:
                continue
            bookmaker = self.odds_service._get_1xbet_bookmaker(item.get("bookmakers", []))
            if not bookmaker:
                continue
            one_xbet_missing = False
            for record in self.odds_service._filter_main_lines(bookmaker):
                record["fixture_id"] = local_match_id
                odds_to_upsert.append(record)

        now_utc = datetime.now(timezone.utc)
        projection = None
        if odds_to_upsert:
            persistence_rows = []
            for record in odds_to_upsert:
                record["last_updated"] = now_utc
                persistence_rows.append(
                    {
                        "fixture_id": record["fixture_id"],
                        "bookmaker_name": record["bookmaker_name"],
                        "market_name": record["market_name"],
                        "selection": record["selection"],
                        "odd_value": record["odd_value"],
                        "myanmar_odd": record.get("myanmar_odd"),
                        "last_updated": record["last_updated"],
                    }
                )
            await self.odds_repository.replace_fixture_odds(db, local_match_id, persistence_rows)
            await db.flush()
            projection = await self.analytics_projection_service.project_odds(db, local_match_id, persistence_rows)
            if not projection["success"]:
                raise ValueError(f"Odds analytics projection rejected: {projection.get('reason', 'reconciliation failure')}")
        else:
            await db.flush()
            projection = await self.analytics_projection_service.project_odds(db, local_match_id, [])

        if not odds_to_upsert:
            reason = "1xbet_data_not_found" if one_xbet_missing else "filtered_no_odds"
            return {"odds": [], "source": "api", "cached": False, "match_started": False, "reason": reason}

        odds_data = [
            {
                "bookmaker": r["bookmaker_name"],
                "market": r["market_name"],
                "selection": r["selection"],
                "odd": r["odd_value"],
                "myanmar_odd": r.get("myanmar_odd"),
                "updated_at": now_utc.isoformat(),
            }
            for r in odds_to_upsert
        ]
        refresh_result = {"status": SUCCESS, "source": "api", "odds": odds_data, "cached": False, "match_started": False, "updated": len(odds_to_upsert), "analytics": projection}
        # The scheduler invalidates this key after its outer transaction commits.
        return refresh_result
