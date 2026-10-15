import logging
import math
import re
from decimal import Decimal, DecimalException, InvalidOperation
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
from app.services.odds_value import canonicalize_odd_value, format_canonical_odd
from myanmar_odds.services.myanmar_odds_service import MyanmarOddsService
from myanmar_odds.utils.handicap_parser import parse_handicap_value

logger = logging.getLogger(__name__)

ODDS_MARKET_CATEGORIES = {
    1: "MATCH_WINNER",
    4: "ASIAN_HANDICAP",
    5: "GOALS_OVER_UNDER",
    45: "CORNERS_OVER_UNDER",
}
REQUIRED_ODDS_MARKET_IDS = frozenset({1, 4, 5})
ODDS_MARKET_DISPLAY_NAMES = {
    "MATCH_WINNER": "Match Winner",
    "ASIAN_HANDICAP": "Asian Handicap",
    "GOALS_OVER_UNDER": "Goals Over/Under",
    "CORNERS_OVER_UNDER": "Corners Over/Under",
}


class OddsCandidateValidationError(ValueError):
    def __init__(self, reason: str, diagnostics: list[dict] | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.diagnostics = diagnostics or []


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
        if market_id not in {4, 5}:
            return

        handicap_value = parse_handicap_value(first_item.get("selection", ""))
        is_goals_over_under = market_id == 5
        effective_favorite_team = None if is_goals_over_under else favorite_team

        try:
            result = self.myanmar_odds_service.convert_to_myanmar_odds(
                home_odds=float(first_item["odd_float"]),
                away_odds=float(second_item["odd_float"]),
                handicap=handicap_value,
                favorite_team=effective_favorite_team,
            )
        except (TypeError, ValueError, OverflowError) as exc:
            raise OddsCandidateValidationError("MYANMAR_CONVERSION_FAILED") from exc

        if result.status != "OK" or not result.market_label or result.market_label == "OUT_OF_RANGE":
            raise OddsCandidateValidationError("MYANMAR_CONVERSION_FAILED")

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
                selection_key = item.get("_selection_key")
                if selection_key:
                    side, line = selection_key.split(maxsplit=1)
                else:
                    parts = str(item["selection"]).strip().lower().split()
                    if len(parts) < 2:
                        continue
                    side, raw_line = parts[0], " ".join(parts[1:])
                    if side not in {"over", "under"}:
                        continue
                    try:
                        line = str(Decimal(raw_line).normalize())
                    except InvalidOperation:
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
        return isinstance(market_id, int) and not isinstance(market_id, bool) and market_id in ODDS_MARKET_CATEGORIES

    def _get_1xbet_bookmaker(self, bookmakers: List[Dict[str, object]]) -> Optional[Dict[str, object]]:
        return next((item for item in bookmakers if item.get("id") == 11), None)

    @staticmethod
    def _validated_market_values(bet: dict, market_id: int) -> list[dict]:
        values = bet.get("values")
        if not isinstance(values, list) or not values:
            raise OddsCandidateValidationError("SUPPORTED_MARKET_VALUES_MISSING")

        normalized = []
        seen_selections = set()
        seen_market_selections = set()
        for value in values:
            if not isinstance(value, dict):
                raise OddsCandidateValidationError("SUPPORTED_MARKET_VALUE_INVALID")
            selection = value.get("value", value.get("selection"))
            odd = value.get("odd")
            if not isinstance(selection, str) or not selection.strip() or odd is None:
                raise OddsCandidateValidationError("SUPPORTED_MARKET_VALUE_INVALID")
            selection = selection.strip()
            normalized_key = selection.casefold()
            if normalized_key in seen_selections:
                raise OddsCandidateValidationError("DUPLICATE_SELECTION")
            seen_selections.add(normalized_key)
            if market_id == 1 and normalized_key not in {"home", "draw", "away"}:
                continue

            try:
                decimal_odd = canonicalize_odd_value(odd)
            except ValueError:
                raise OddsCandidateValidationError("INVALID_ODD")
            odd_float = float(decimal_odd)
            if not math.isfinite(odd_float) or odd_float <= 0:
                raise OddsCandidateValidationError("INVALID_ODD")

            if market_id == 1:
                selection_key = selection.casefold()
                if selection_key not in {"home", "draw", "away"}:
                    raise OddsCandidateValidationError("INVALID_MATCH_WINNER_SELECTION")
            elif market_id in {5, 45}:
                if re.fullmatch(r"(?:over|under)\s+.+", selection, re.IGNORECASE) is None:
                    raise OddsCandidateValidationError("INVALID_TOTALS_SELECTION")
                side, line = selection.split(maxsplit=1)
                try:
                    parsed_line = Decimal(line)
                except InvalidOperation:
                    raise OddsCandidateValidationError("INVALID_TOTALS_LINE") from None
                if not parsed_line.is_finite():
                    raise OddsCandidateValidationError("INVALID_TOTALS_LINE")
                selection_key = f"{side.casefold()} {parsed_line.normalize()}"
            else:
                match = re.fullmatch(r"(home|away)\s+([+-]?\d+(?:\.\d+)?)", selection, re.IGNORECASE)
                if match is None:
                    raise OddsCandidateValidationError("INVALID_HANDICAP_SELECTION")
                parsed_line = Decimal(match.group(2))
                if not parsed_line.is_finite():
                    raise OddsCandidateValidationError("INVALID_HANDICAP_LINE")
                selection_key = f"{match.group(1).casefold()} {parsed_line.normalize()}"

            semantic_key = selection_key
            if semantic_key in seen_market_selections:
                raise OddsCandidateValidationError("DUPLICATE_SELECTION")
            seen_market_selections.add(semantic_key)
            normalized.append(
                {
                    "selection": selection,
                    "odd": format_canonical_odd(decimal_odd),
                    "odd_float": odd_float,
                    "_selection_key": selection_key,
                }
            )
        return normalized

    def build_candidate(self, bookmaker_data: dict) -> tuple[list[dict], list[dict]]:
        if not isinstance(bookmaker_data, dict) or bookmaker_data.get("id") != 11:
            raise OddsCandidateValidationError("BOOKMAKER_ID_MISMATCH")
        bookmaker_name = bookmaker_data.get("name")
        if not isinstance(bookmaker_name, str) or bookmaker_name.strip().casefold() != "1xbet":
            raise OddsCandidateValidationError(
                "BOOKMAKER_NAME_MISMATCH",
                [{"bookmaker_id": 11, "raw_bookmaker_name": bookmaker_name}],
            )

        bets = bookmaker_data.get("bets")
        if not isinstance(bets, list) or not bets:
            raise OddsCandidateValidationError("NO_SUPPORTED_MARKETS")

        diagnostics = []
        parsed_bets = {}
        for bet in bets:
            if not isinstance(bet, dict):
                raise OddsCandidateValidationError("MARKET_ENTRY_INVALID")
            market_id = bet.get("id")
            raw_name = bet.get("name")
            if not isinstance(market_id, int) or isinstance(market_id, bool):
                diagnostics.append({"market_id": market_id, "raw_market_name": raw_name, "reason": "INVALID_MARKET_ID"})
                raise OddsCandidateValidationError("INVALID_MARKET_ID", diagnostics)
            if market_id not in ODDS_MARKET_CATEGORIES:
                diagnostics.append({"market_id": market_id, "raw_market_name": raw_name, "reason": "UNKNOWN_MARKET"})
                continue
            if market_id in parsed_bets:
                raise OddsCandidateValidationError(
                    "DUPLICATE_MARKET_ID",
                    [{"market_id": market_id, "raw_market_name": raw_name}],
                )
            parsed_bets[market_id] = {
                "name": raw_name,
                "values": self._validated_market_values(bet, market_id),
            }

        if not parsed_bets:
            raise OddsCandidateValidationError("NO_SUPPORTED_MARKETS")
        missing_market_ids = sorted(REQUIRED_ODDS_MARKET_IDS - parsed_bets.keys())
        if missing_market_ids:
            raise OddsCandidateValidationError(
                "MISSING_REQUIRED_MARKETS",
                [
                    *diagnostics,
                    *(
                        {"market_id": market_id, "reason": "REQUIRED_MARKET_MISSING"}
                        for market_id in missing_market_ids
                    ),
                ],
            )

        favorite_team = None
        match_winner = parsed_bets.get(1)
        if match_winner:
            outcomes = {item["selection"].casefold(): item for item in match_winner["values"]}
            required = {"home", "draw", "away"}
            if set(outcomes) != required:
                raise OddsCandidateValidationError("INCOMPLETE_MATCH_WINNER")
            home_odd = outcomes["home"]["odd_float"]
            away_odd = outcomes["away"]["odd_float"]
            if home_odd != away_odd:
                favorite_team = "HOME" if home_odd < away_odd else "AWAY"

        candidate = []
        for market_id, bet in parsed_bets.items():
            category = ODDS_MARKET_CATEGORIES[market_id]
            values = bet["values"]
            if market_id in {4, 5, 45}:
                line_sides: dict[str, set[str]] = {}
                for value in values:
                    side, line = value["_selection_key"].split(maxsplit=1)
                    line_sides.setdefault(line, set()).add(side)
                expected_sides = {"home", "away"} if market_id == 4 else {"over", "under"}
                if not line_sides or any(sides != expected_sides for sides in line_sides.values()):
                    raise OddsCandidateValidationError(f"INCOMPLETE_{category}")
            if market_id in {1, 4, 5, 45}:
                selected = self._select_main_line_by_id(
                    market_id,
                    category,
                    values,
                    favorite_team=favorite_team,
                )
                if not selected:
                    raise OddsCandidateValidationError(f"INCOMPLETE_{category}")
            else:
                raise OddsCandidateValidationError("UNSUPPORTED_MARKET_ID")

            for line in selected:
                if market_id in {4, 5}:
                    label = line.get("myanmar_odd")
                    if not isinstance(label, str) or not label.strip() or label == "OUT_OF_RANGE":
                        raise OddsCandidateValidationError("MYANMAR_CONVERSION_FAILED")
                else:
                    line["myanmar_odd"] = None
                candidate.append(
                    {
                        "bookmaker_name": "1xBet",
                        "market_name": category,
                        "selection": line["selection"],
                        "odd_value": line["odd"],
                        "myanmar_odd": line.get("myanmar_odd"),
                        "market_id": market_id,
                        "raw_market_name": bet["name"],
                    }
                )

        candidate_keys = [
            (row["bookmaker_name"], row["market_name"], row["selection"])
            for row in candidate
        ]
        if len(candidate_keys) != len(set(candidate_keys)):
            raise OddsCandidateValidationError("DUPLICATE_CANONICAL_SELECTION")
        return candidate, diagnostics

    def _filter_main_lines(self, bookmaker_data: Dict[str, object]) -> List[Dict[str, object]]:
        rows, _diagnostics = self.build_candidate(bookmaker_data)
        return rows

    @staticmethod
    def public_market_name(market_name: str) -> str:
        return ODDS_MARKET_DISPLAY_NAMES.get(market_name, market_name)

    @staticmethod
    def is_valid_snapshot(rows: list[Odds]) -> bool:
        if not rows:
            return False

        grouped: dict[str, dict[str, Odds]] = {}
        seen = set()
        timestamps = set()
        for row in rows:
            if row.bookmaker_name != "1xBet" or row.market_name not in ODDS_MARKET_DISPLAY_NAMES:
                return False
            if not isinstance(row.selection, str) or not row.selection:
                return False
            if row.last_updated is None:
                return False
            timestamps.add(row.last_updated)
            try:
                canonicalize_odd_value(row.odd_value)
            except ValueError:
                return False
            if row.market_name in {"ASIAN_HANDICAP", "GOALS_OVER_UNDER"}:
                if not row.myanmar_odd or row.myanmar_odd == "OUT_OF_RANGE":
                    return False
            elif row.myanmar_odd is not None:
                return False
            key = (row.market_name, row.selection)
            if key in seen:
                return False
            seen.add(key)
            grouped.setdefault(row.market_name, {})[row.selection] = row

        required_markets = {
            ODDS_MARKET_CATEGORIES[market_id]
            for market_id in REQUIRED_ODDS_MARKET_IDS
        }
        if not required_markets.issubset(grouped):
            return False

        if len(timestamps) != 1:
            return False

        for market_name, market_rows in grouped.items():
            selections = set(market_rows)
            if market_name == "MATCH_WINNER":
                if selections != {"Home", "Draw", "Away"}:
                    return False
            elif market_name in {"GOALS_OVER_UNDER", "CORNERS_OVER_UNDER"}:
                if len(selections) != 2 or not any(value.startswith("Over ") for value in selections) or not any(value.startswith("Under ") for value in selections):
                    return False
                try:
                    over_line = Decimal(next(value[5:] for value in selections if value.startswith("Over ")))
                    under_line = Decimal(next(value[6:] for value in selections if value.startswith("Under ")))
                    if not over_line.is_finite() or not under_line.is_finite():
                        return False
                    lines_match = over_line.normalize() == under_line.normalize()
                except DecimalException:
                    return False
                if not lines_match:
                    return False
            elif market_name == "ASIAN_HANDICAP":
                if len(selections) != 2 or not any(value.startswith("Home ") for value in selections) or not any(value.startswith("Away ") for value in selections):
                    return False
                try:
                    home_line = Decimal(next(value[5:] for value in selections if value.startswith("Home ")))
                    away_line = Decimal(next(value[5:] for value in selections if value.startswith("Away ")))
                    if not home_line.is_finite() or not away_line.is_finite():
                        return False
                    lines_match = home_line.normalize() == away_line.normalize()
                except DecimalException:
                    return False
                if not lines_match:
                    return False
        return True

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
                    "market": self.public_market_name(o.market_name),
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
