"""Stateless Myanmar Odds conversion service."""

from myanmar_odds.constants.base_index_map import get_base_index
from myanmar_odds.constants.ladder_map import ladder_label_for_index
from myanmar_odds.models.myanmar_odds_result import MyanmarOddsResult
from myanmar_odds.utils.handicap_parser import parse_handicap_value


class MyanmarOddsService:
    """Pure-domain Myanmar Odds conversion engine.

    The service intentionally does not touch database logic, networking, or
    existing odds sync code. It only converts raw odds inputs into the Myanmar
    ladder representation.
    """

    @staticmethod
    def calculate_diff(home_odds: float, away_odds: float) -> int:
        """Return the odds difference in index units."""
        diff = abs(float(home_odds) - float(away_odds)) * 100.0
        return int(round(diff))

    @staticmethod
    def detect_market_side(home_odds: float, away_odds: float) -> str:
        """Determine the favorite side from the lower odds value."""
        if home_odds == away_odds:
            return "DRAW"
        return "HOME" if home_odds < away_odds else "AWAY"

    @staticmethod
    def other_side_label(label: str) -> str:
        """Return the opposite ladder label for the non-favorite side."""
        text = str(label).strip()

        if text.endswith("=") or text == "D":
            return text

        if "+" in text:
            return text.replace("+", "-", 1)

        if "-" in text:
            return text.replace("-", "+", 1)

        return text

    @staticmethod
    def shift_index(base_index: int, diff: int, market_side: str) -> int:
        """Calculate the ladder market index from the base handicap and difference."""
        if market_side in {"HOME", "AWAY"}:
            return int(base_index) + int(diff)

        return int(base_index)

    def convert_to_myanmar_odds(
        self,
        home_odds: float,
        away_odds: float,
        handicap: float | int | str,
        favorite_team: str | None = None,
    ) -> MyanmarOddsResult:
        """Convert a raw Asian Handicap odds pair into Myanmar odds output."""
        handicap_value = parse_handicap_value(handicap)
        diff = self.calculate_diff(home_odds, away_odds)

        if favorite_team in {"HOME", "AWAY"}:
            market_side = favorite_team
        else:
            market_side = self.detect_market_side(home_odds, away_odds)

        favorite_odds = home_odds if market_side == "HOME" else away_odds
        other_odds = away_odds if market_side == "HOME" else home_odds
        favorite_is_supported = favorite_odds < other_odds

        if handicap_value == 0:
            if diff == 0:
                market_label = "D"
                market_index = 0
            else:
                market_label = f"-{diff}" if market_side == "HOME" else f"+{diff}"
                market_index = diff

            return MyanmarOddsResult(
                home_odds=float(home_odds),
                away_odds=float(away_odds),
                market_side=market_side,
                handicap=float(handicap_value),
                base_index=0,
                diff=int(diff),
                market_index=int(market_index),
                market_label=market_label,
                status="OK",
            )

        base_index = get_base_index(handicap_value)

        if favorite_is_supported:
            market_index = base_index + diff
        else:
            market_index = base_index - diff

        market_label = ladder_label_for_index(market_index)

        status = "OK"
        if market_label == "OUT_OF_RANGE":
            status = "OUT_OF_RANGE"

        return MyanmarOddsResult(
            home_odds=float(home_odds),
            away_odds=float(away_odds),
            market_side=market_side,
            handicap=float(handicap_value),
            base_index=int(base_index),
            diff=int(diff),
            market_index=int(market_index),
            market_label=market_label,
            status=status,
        )
