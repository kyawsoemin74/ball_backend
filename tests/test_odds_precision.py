from decimal import Decimal

import pytest

from app.services.analytics_projection_service import AnalyticsProjectionService
from app.services.odds_service import OddsService
from app.services.odds_value import canonicalize_odd_value, format_canonical_odd


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1.800000", Decimal("1.8")),
        ("1.234567", Decimal("1.234567")),
        ("999999.999999", Decimal("999999.999999")),
    ],
)
def test_odds_precision_accepts_six_places_and_trailing_zero_equivalents(value, expected):
    assert canonicalize_odd_value(value) == expected
    assert format_canonical_odd(value) == format(expected, "f")


@pytest.mark.parametrize("value", ["1.1234567", "1000000", "999999.9999991", "0"])
def test_odds_precision_rejects_excess_precision_range_and_nonpositive_values(value):
    with pytest.raises(ValueError):
        canonicalize_odd_value(value)


def test_candidate_stores_the_canonical_odds_representation():
    service = OddsService(client=object())
    candidate, _ = service.build_candidate(
        {
            "id": 11,
            "name": "1xBet",
            "bets": [
                {
                    "id": 1,
                    "name": "Match Winner",
                    "values": [
                        {"value": "Home", "odd": "1.800000"},
                        {"value": "Draw", "odd": "3.200000"},
                        {"value": "Away", "odd": "4.600000"},
                    ],
                },
                {
                    "id": 4,
                    "name": "Asian Handicap",
                    "values": [
                        {"value": "Home -0.5", "odd": "1.91"},
                        {"value": "Away -0.5", "odd": "1.95"},
                    ],
                },
                {
                    "id": 5,
                    "name": "Goals Over/Under",
                    "values": [
                        {"value": "Over 2.5", "odd": "1.95"},
                        {"value": "Under 2.5", "odd": "1.90"},
                    ],
                },
            ],
        }
    )

    assert {
        row["odd_value"]
        for row in candidate
        if row["market_name"] == "MATCH_WINNER"
    } == {"1.8", "3.2", "4.6"}


@pytest.mark.asyncio
async def test_analytics_projection_uses_the_same_canonical_decimal():
    projected_rows = []

    class OddsRepository:
        async def replace_by_match(self, _db, _match_id, rows):
            projected_rows.extend(rows)

    service = AnalyticsProjectionService()
    service.odds_repository = OddsRepository()
    source_row = {
        "bookmaker_name": "1xBet",
        "market_name": "MATCH_WINNER",
        "selection": "Home",
        "odd_value": "1.800000",
        "myanmar_odd": None,
        "last_updated": None,
    }

    result = await service.project_odds(None, 42, [source_row])

    assert result["success"] is True
    assert projected_rows[0]["odd_value"] == canonicalize_odd_value(
        source_row["odd_value"]
    )
    assert projected_rows[0]["odd_value"] == Decimal("1.8")
