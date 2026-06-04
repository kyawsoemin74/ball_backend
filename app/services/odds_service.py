import logging
from typing import Dict, List, Optional

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache import make_cache_key
from app.models.match import Match
from app.models.odds import Odds
from app.services.base.football_client import FootballAPIClient
from app.services.cache_service import CacheService

logger = logging.getLogger(__name__)


class OddsService:
    def __init__(self, client: FootballAPIClient, cache_service: CacheService | None = None) -> None:
        self.client = client
        self.cache_service = cache_service or CacheService()

    def _select_main_line_by_id(self, market_id: int, market_values: List[Dict[str, object]]) -> List[Dict[str, object]]:
        if market_id == 1:
            outcomes = {str(item["selection"]).strip().lower(): item for item in market_values}
            required = {"home", "draw", "away"}
            if required.issubset(outcomes):
                return [outcomes["home"], outcomes["draw"], outcomes["away"]]
            return []
        if market_id in {5, 45}:
            lines = {}
            for item in market_values:
                parts = str(item["selection"]).strip().lower().split()
                if len(parts) < 2:
                    continue
                side, line = parts[0], " ".join(parts[1:])
                if side not in {"over", "under"}:
                    continue
                lines.setdefault(line, {})[side] = item
            valid_lines = [group for group in lines.values() if "over" in group and "under" in group]
            if not valid_lines:
                return []
            selected = min(valid_lines, key=lambda group: (abs(float(group["over"]["odd_float"]) - 2.0) + abs(float(group["under"]["odd_float"]) - 2.0)) / 2.0)
            return [selected["over"], selected["under"]]
        if market_id == 4:
            pairs = {}
            for item in market_values:
                selection = str(item["selection"]).strip()
                lower = selection.lower()
                if lower.startswith("home "):
                    side, handicap_text = "home", selection[5:].strip()
                elif lower.startswith("away "):
                    side, handicap_text = "away", selection[5:].strip()
                else:
                    continue
                try:
                    handicap = float(handicap_text)
                except ValueError:
                    continue
                spread = abs(handicap)
                pairs.setdefault(spread, {})[side] = item
            valid_pairs = [group for group in pairs.values() if "home" in group and "away" in group]
            if not valid_pairs:
                return []
            selected = min(valid_pairs, key=lambda group: (abs(float(group["home"]["odd_float"]) - 2.0) + abs(float(group["away"]["odd_float"]) - 2.0)) / 2.0)
            return [selected["home"], selected["away"]]
        return []

    def _is_target_market(self, market_id: int) -> bool:
        return market_id in {1, 4, 5, 45}

    def _get_1xbet_bookmaker(self, bookmakers: List[Dict[str, object]]) -> Optional[Dict[str, object]]:
        return next((item for item in bookmakers if item.get("id") == 11), None)

    def _filter_main_lines(self, bookmaker_data: Dict[str, object]) -> List[Dict[str, object]]:
        filtered_odds = []
        for bet in bookmaker_data.get("bets", []):
            market_id = bet.get("id")
            market_name = bet.get("name", "")
            if not self._is_target_market(market_id):
                continue
            market_values = []
            for value in bet.get("values", []):
                selection = value.get("value")
                odd_value = value.get("odd")
                if selection is None or odd_value is None:
                    continue
                try:
                    odd_float = float(odd_value)
                except (ValueError, TypeError):
                    continue
                market_values.append({"selection": str(selection).strip(), "odd": str(odd_value), "odd_float": odd_float})
            for line in self._select_main_line_by_id(market_id, market_values):
                filtered_odds.append({"fixture_id": None, "bookmaker_name": bookmaker_data.get("name", "1xBet"), "market_name": market_name, "selection": line["selection"], "odd_value": line["odd"]})
        return filtered_odds

    async def get_match_odds(self, match_id: int) -> Optional[dict]:
        return await self.client.get("/odds", params={"fixture": match_id})

    async def get_cached_odds(self, db: AsyncSession, fixture_id: int) -> dict:
        from datetime import datetime, timedelta, timezone
        from app.core.config import settings

        cache_key = make_cache_key("match", fixture_id, "odds")
        cached = await self.cache_service.get_json(cache_key)
        if cached is not None:
            return cached

        match = (await db.execute(select(Match).where(Match.match_id == fixture_id))).scalar_one_or_none()
        if not match:
            return {"error": "Match not found"}

        match_started = match.status not in ["NS", "TBD", "PST"]
        existing_odds = (await db.execute(select(Odds).where(Odds.fixture_id == fixture_id))).scalars().all()

        if existing_odds:
            latest_update = max((o.last_updated for o in existing_odds if o.last_updated), default=None)
            if (latest_update and (datetime.now(timezone.utc) - latest_update) < timedelta(minutes=30)) or match_started:
                odds_data = [{"bookmaker": o.bookmaker_name, "market": o.market_name, "selection": o.selection, "odd": o.odd_value, "updated_at": o.last_updated.isoformat() if o.last_updated else None} for o in existing_odds]
                result = {"source": "database", "odds": odds_data, "cached": True, "match_started": match_started}
                await self.cache_service.set_json(cache_key, result, 120)
                return result

        if match_started:
            odds_data = [{"bookmaker": o.bookmaker_name, "market": o.market_name, "selection": o.selection, "odd": o.odd_value, "updated_at": o.last_updated.isoformat() if o.last_updated else None} for o in existing_odds]
            result = {"source": "database", "odds": odds_data, "cached": True, "match_started": True, "reason": "match_started"}
            await self.cache_service.set_json(cache_key, result, 120)
            return result

        result = await self.get_match_odds(fixture_id)
        if not result or "response" not in result:
            return {"error": "API error"}

        responses = result.get("response", [])
        if not responses:
            return {"odds": [], "source": "api", "cached": False, "match_started": match_started, "reason": "no_data"}

        odds_to_upsert = []
        one_xbet_missing = True
        for item in responses:
            if item.get("fixture", {}).get("id") != fixture_id:
                continue
            bookmaker = self._get_1xbet_bookmaker(item.get("bookmakers", []))
            if not bookmaker:
                continue
            one_xbet_missing = False
            for record in self._filter_main_lines(bookmaker):
                record["fixture_id"] = fixture_id
                odds_to_upsert.append(record)

        if odds_to_upsert:
            await db.execute(delete(Odds).where(Odds.fixture_id == fixture_id))
            for record in odds_to_upsert:
                db.add(Odds(**record))
            await db.flush()
        else:
            await db.flush()

        if not odds_to_upsert:
            reason = "1xbet_data_not_found" if one_xbet_missing else "filtered_no_odds"
            result = {"odds": [], "source": "api", "cached": False, "match_started": match_started, "reason": reason}
            await self.cache_service.set_json(cache_key, result, 120)
            return result

        odds_data = [{"bookmaker": r["bookmaker_name"], "market": r["market_name"], "selection": r["selection"], "odd": r["odd_value"], "updated_at": None} for r in odds_to_upsert]
        result = {"source": "api", "odds": odds_data, "cached": False, "match_started": match_started, "updated": len(odds_to_upsert)}
        await self.cache_service.set_json(cache_key, result, 120)
        return result
