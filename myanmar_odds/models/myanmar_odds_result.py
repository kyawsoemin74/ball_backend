"""Myanmar Odds result model."""

from dataclasses import dataclass


@dataclass(frozen=True)
class MyanmarOddsResult:
    home_odds: float
    away_odds: float
    market_side: str
    handicap: float
    base_index: int
    diff: int
    market_index: int
    market_label: str
    status: str = "OK"
