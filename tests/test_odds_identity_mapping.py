import pytest

from app.services.odds_service import (
    ODDS_MARKET_CATEGORIES,
    OddsService,
)
from app.models.odds import Odds


class MyanmarOddsProbe:
    def convert_to_myanmar_odds(self, **kwargs):
        return type(
            "MyanmarResult",
            (),
            {"status": "OK", "market_label": "HOME", "market_side": "HOME"},
        )()

    @staticmethod
    def other_side_label(_label):
        return "AWAY"


def odds_service():
    service = OddsService.__new__(OddsService)
    service.myanmar_odds_service = MyanmarOddsProbe()
    return service


def test_provider_market_ids_map_to_distinct_canonical_categories():
    assert ODDS_MARKET_CATEGORIES[1] == "MATCH_WINNER"
    assert ODDS_MARKET_CATEGORIES[4] == "ASIAN_HANDICAP"
    assert ODDS_MARKET_CATEGORIES[5] == "GOALS_OVER_UNDER"
    assert ODDS_MARKET_CATEGORIES[45] == "CORNERS_OVER_UNDER"


@pytest.mark.parametrize(
    ("market_id", "expected_market"),
    [(5, "GOALS_OVER_UNDER"), (45, "CORNERS_OVER_UNDER")],
)
def test_equal_selection_in_goal_and_corner_markets_remains_distinct(
    market_id, expected_market
):
    bets = [
        {
            "id": 1,
            "name": "Match Winner",
            "values": [
                {"value": "Home", "odd": "1.80"},
                {"value": "Draw", "odd": "3.20"},
                {"value": "Away", "odd": "4.10"},
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
    ]
    if market_id == 45:
        bets.append(
            {
                "id": market_id,
                "name": "Over/Under",
                "values": [
                    {"value": "Over 2.5", "odd": "1.90"},
                    {"value": "Under 2.5", "odd": "1.90"},
                ],
            }
        )

    candidate, diagnostics = odds_service().build_candidate(
        {
            "id": 11,
            "name": "1xBet",
            "bets": bets,
        }
    )

    assert diagnostics == []
    selected_market = [row for row in candidate if row["market_name"] == expected_market]
    assert {row["selection"] for row in selected_market} == {"Over 2.5", "Under 2.5"}


def test_unknown_market_is_not_silently_treated_as_supported():
    candidate, diagnostics = odds_service().build_candidate(
        {
            "id": 11,
            "name": "1xBet",
            "bets": [
                {
                    "id": 1,
                    "name": "Match Winner",
                    "values": [
                        {"value": "Home", "odd": "1.80"},
                        {"value": "Draw", "odd": "3.20"},
                        {"value": "Away", "odd": "4.10"},
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
                {
                    "id": 999,
                    "name": "Unsupported",
                    "values": [{"value": "Home", "odd": "1.80"}],
                },
            ],
        }
    )

    assert {row["market_id"] for row in candidate} == {1, 4, 5}
    assert all(row["market_id"] != 999 for row in candidate)
    assert diagnostics == [
        {
            "market_id": 999,
            "raw_market_name": "Unsupported",
            "reason": "UNKNOWN_MARKET",
        }
    ]


def test_persistence_business_identity_excludes_the_mutable_odd_value():
    unique_constraint = next(
        constraint
        for constraint in Odds.__table__.constraints
        if getattr(constraint, "name", None)
        == "uq_odds_fixture_bookmaker_market_selection"
    )

    assert list(unique_constraint.columns.keys()) == [
        "fixture_id",
        "bookmaker_name",
        "market_name",
        "selection",
    ]
    assert "odd_value" not in unique_constraint.columns
