import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from app.providers.odds_provider import OddsProvider
from app.repositories.odds_repository import OddsRepository
from app.services.cache_service import CacheService

if TYPE_CHECKING:
    from app.services.odds_service import OddsService

logger = logging.getLogger(__name__)


class OddsSyncService:
    """Write/refresh orchestration for odds data without owning read or transport logic."""

    def __init__(
        self,
        odds_service: "OddsService",
        cache_service: CacheService | None = None,
        odds_provider: OddsProvider | None = None,
        odds_repository: OddsRepository | None = None,
    ) -> None:
        self.odds_service = odds_service
        self.cache_service = cache_service or CacheService()
        self.odds_provider = odds_provider or OddsProvider(self.odds_service.client)
        self.odds_repository = odds_repository or OddsRepository()

    async def refresh_odds(self, db, fixture_id: int, cache_key: str, pre_match_ttl: int) -> dict:
        result = await self.odds_provider.get_match_odds(fixture_id)
        if not result or "response" not in result:
            return {"error": "API error"}

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
                record["fixture_id"] = fixture_id
                odds_to_upsert.append(record)

        now_utc = datetime.now(timezone.utc)
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
            await self.odds_repository.replace_fixture_odds(db, fixture_id, persistence_rows)
            await db.flush()
        else:
            await db.flush()

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
        refresh_result = {"source": "api", "odds": odds_data, "cached": False, "match_started": False, "updated": len(odds_to_upsert)}
        await self.cache_service.set_json(cache_key, refresh_result, pre_match_ttl)
        return refresh_result
