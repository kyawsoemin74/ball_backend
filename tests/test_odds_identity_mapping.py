import asyncio

import pytest

from app.services.odds_service import (
    ODDS_MARKET_CATEGORIES,
    OddsService,
)
from app.models.odds import Odds
from app.repositories.odds_repository import OddsRepository


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


def test_giving_team_columns_are_nullable_and_constrained():
    from app.models.analytics import AnalyticsMatchOddsSnapshot

    for model, constraint_name in (
        (Odds, "ck_odds_giving_team"),
        (AnalyticsMatchOddsSnapshot, "ck_analytics_odds_giving_team"),
    ):
        assert model.__table__.c.giving_team.nullable is True
        constraint = next(
            constraint
            for constraint in model.__table__.constraints
            if getattr(constraint, "name", None) == constraint_name
        )
        assert "HOME" in str(constraint.sqltext)
        assert "AWAY" in str(constraint.sqltext)


def test_odds_repository_upsert_includes_giving_team_and_preserves_ah_odd_text():
    class CapturingDB:
        statement = None

        async def execute(self, statement):
            self.statement = statement

    db = CapturingDB()
    row = {
        "fixture_id": 42,
        "bookmaker_name": "1xBet",
        "market_name": "ASIAN_HANDICAP",
        "selection": " Home -0.25 ",
        "odd_value": "1.9100",
        "myanmar_odd": "-90",
        "giving_team": "HOME",
    }

    asyncio.run(OddsRepository().upsert_many(db, [row]))

    compiled = db.statement.compile()
    assert "giving_team" in str(db.statement)
    assert "1.9100" in compiled.params.values()
    assert "HOME" in compiled.params.values()
    assert " Home -0.25 " in compiled.params.values()
