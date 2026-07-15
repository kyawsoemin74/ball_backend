import logging
from typing import Dict, List, Optional

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache import make_cache_key
from app.models.match import Match
from app.models.odds import Odds
from app.providers.odds_provider import OddsProvider
from app.services.base.football_client import FootballAPIClient
from app.services.cache_service import CacheService
from app.services.odds_sync_service import OddsSyncService
from myanmar_odds.services.myanmar_odds_service import MyanmarOddsService
from myanmar_odds.utils.handicap_parser import parse_handicap_value

logger = logging.getLogger(__name__)


class OddsService:
    def __init__(
        self,
        client: FootballAPIClient,
        cache_service: CacheService | None = None,
        odds_provider: OddsProvider | None = None,
    ) -> None:
        self.client = client
        self.cache_service = cache_service or CacheService()
        self.odds_provider = odds_provider or OddsProvider(client)
        self.myanmar_odds_service = MyanmarOddsService()
        self.odds_sync_service = OddsSyncService(self, cache_service=self.cache_service, odds_provider=self.odds_provider)

    @staticmethod
    def _canonical_handicap_key(handicap_text: str, handicap: float, side: Optional[str] = None) -> str:
        raw_text = str(handicap_text).strip()

        try:
            value = float(raw_text)
        except (TypeError, ValueError):
            value = float(handicap)

        if value == 0:
            return "0"

        raw_sign = raw_text[0] if raw_text.startswith(("+", "-")) else ""
        sign_char = raw_sign if raw_sign else ("+" if value > 0 else "")

        normalized = f"{abs(value):g}"
        if sign_char in {"+", "-"}:
            return f"{sign_char}{normalized}"
        return f"{value:g}"

    def _build_handicap_pairs(self, market_values: List[Dict[str, object]]) -> Dict[str, Dict[str, Dict[str, object]]]:
        pairs: Dict[str, Dict[str, Dict[str, object]]] = {}
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

            key = self._canonical_handicap_key(handicap_text, handicap, side=side)
            pairs.setdefault(key, {})[side] = item
        return pairs

    def _attach_myanmar_odd_labels(
        self,
        market_name: str,
        market_id: int,
        first_item: Dict[str, object],
        second_item: Dict[str, object],
        favorite_team: Optional[str] = None,
    ) -> None:
        allowed_market_names = {"Asian Handicap", "Goals Over/Under"}
        if str(market_name).strip() not in allowed_market_names:
            return

        if market_id not in {4, 5, 45}:
            return

        handicap_value = parse_handicap_value(first_item.get("selection", ""))
        is_goals_over_under = str(market_name).strip() == "Goals Over/Under"
        effective_favorite_team = None if is_goals_over_under else favorite_team

        result = self.myanmar_odds_service.convert_to_myanmar_odds(
            home_odds=float(first_item["odd_float"]),
            away_odds=float(second_item["odd_float"]),
            handicap=handicap_value,
            favorite_team=effective_favorite_team,
        )

        label = result.market_label
        opposite_label = self.myanmar_odds_service.other_side_label(label)

        if is_goals_over_under:
            first_item["myanmar_odd"] = label
            second_item["myanmar_odd"] = opposite_label
            return

        if favorite_team == "HOME":
            first_item["myanmar_odd"] = label
            second_item["myanmar_odd"] = opposite_label
        elif favorite_team == "AWAY":
            first_item["myanmar_odd"] = opposite_label
            second_item["myanmar_odd"] = label
        elif result.market_side == "HOME":
            first_item["myanmar_odd"] = label
            second_item["myanmar_odd"] = opposite_label
        elif result.market_side == "AWAY":
            first_item["myanmar_odd"] = opposite_label
            second_item["myanmar_odd"] = label
        else:
            first_item["myanmar_odd"] = label
            second_item["myanmar_odd"] = label

    def _select_main_line_by_id(
        self,
        market_id: int,
        market_name: str,
        market_values: List[Dict[str, object]],
        favorite_team: Optional[str] = None,
    ) -> List[Dict[str, object]]:
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
            self._attach_myanmar_odd_labels(market_name, market_id, selected["over"], selected["under"], favorite_team=favorite_team)
            return [selected["over"], selected["under"]]
        if market_id == 4:
            pairs = self._build_handicap_pairs(market_values)
            valid_pairs = [group for group in pairs.values() if "home" in group and "away" in group]
            if not valid_pairs:
                return []
            selected = min(valid_pairs, key=lambda group: (abs(float(group["home"]["odd_float"]) - 2.0) + abs(float(group["away"]["odd_float"]) - 2.0)) / 2.0)
            self._attach_myanmar_odd_labels(market_name, market_id, selected["home"], selected["away"], favorite_team=favorite_team)
            return [selected["home"], selected["away"]]
        return []

    def _is_target_market(self, market_id: int) -> bool:
        return market_id in {1, 4, 5, 45}

    def _get_1xbet_bookmaker(self, bookmakers: List[Dict[str, object]]) -> Optional[Dict[str, object]]:
        return next((item for item in bookmakers if item.get("id") == 11), None)

    def _filter_main_lines(self, bookmaker_data: Dict[str, object]) -> List[Dict[str, object]]:
        filtered_odds = []
        favorite_team = None

        for bet in bookmaker_data.get("bets", []):
            if bet.get("id") == 1:
                outcomes = {}
                for item in bet.get("values", []):
                    label = str(item.get("value", item.get("selection", ""))).strip().lower()
                    if label in {"home", "draw", "away"}:
                        outcomes[label] = item

                home_item = outcomes.get("home")
                away_item = outcomes.get("away")
                if home_item and away_item:
                    try:
                        home_odd = float(home_item.get("odd", 0))
                        away_odd = float(away_item.get("odd", 0))
                    except (TypeError, ValueError):
                        home_odd = away_odd = 0.0

                    favorite_team = "HOME" if home_odd < away_odd else "AWAY"
                    break

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
            for line in self._select_main_line_by_id(market_id, market_name, market_values, favorite_team=favorite_team):
                filtered_odds.append({
                    "fixture_id": None,
                    "bookmaker_name": bookmaker_data.get("name", "1xBet"),
                    "market_name": market_name,
                    "selection": line["selection"],
                    "odd_value": line["odd"],
                    "myanmar_odd": line.get("myanmar_odd"),
                })
        return filtered_odds

    async def get_match_odds(self, match_id: int) -> Optional[dict]:
        return {"error": "Read-only odds access", "match_id": match_id}

    async def get_cached_odds(self, db: AsyncSession, fixture_id: int) -> dict:
        cache_key = make_cache_key("match", fixture_id, "odds")
        cached = await self.cache_service.get_json(cache_key)
        if cached is not None:
            logger.debug("ODDS_CACHE_HIT", extra={"fixture_id": fixture_id, "status": "UNKNOWN", "ttl": None})
            return cached

        match = (await db.execute(select(Match).where(Match.match_id == fixture_id))).scalar_one_or_none()
        if not match:
            return {"error": "Match not found"}

        status = (getattr(match, "status", None) or "").upper()
        existing_odds = (await db.execute(select(Odds).where(Odds.fixture_id == fixture_id))).scalars().all()

        def _serialize_odds(rows: list[Odds]) -> list[dict]:
            return [
                {
                    "bookmaker": o.bookmaker_name,
                    "market": o.market_name,
                    "selection": o.selection,
                    "odd": o.odd_value,
                    "myanmar_odd": o.myanmar_odd,
                    "updated_at": o.last_updated.isoformat() if o.last_updated else None,
                }
                for o in rows
            ]

        result = {
            "source": "database",
            "odds": _serialize_odds(existing_odds),
            "cached": True,
            "match_started": status not in {"NS", "TBD", "PST"},
        }
        logger.debug("ODDS_DB_READ_ONLY", extra={"fixture_id": fixture_id, "status": status, "ttl": None})
        return result
