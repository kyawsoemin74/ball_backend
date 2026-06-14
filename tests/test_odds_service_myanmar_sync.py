from app.services.odds_service import OddsService
from myanmar_odds.services.myanmar_odds_service import MyanmarOddsService


class _DummyClient:
    pass


def test_select_main_line_by_id_generates_myanmar_odd_for_asian_handicap_pairs():
    service = OddsService(client=_DummyClient())

    market_values = [
        {"selection": "Home -1.25", "odd": "2.02", "odd_float": 2.02},
        {"selection": "Away -1.25", "odd": "1.88", "odd_float": 1.88},
    ]

    lines = service._select_main_line_by_id(4, "Asian Handicap", market_values)

    assert len(lines) == 2
    assert all("myanmar_odd" in item for item in lines)
    assert any(item["selection"] == "Home -1.25" and item["myanmar_odd"] for item in lines)
    assert any(item["selection"] == "Away -1.25" and item["myanmar_odd"] for item in lines)


def test_select_main_line_by_id_generates_myanmar_odd_for_over_under_pairs():
    service = OddsService(client=_DummyClient())

    market_values = [
        {"selection": "Over 2.25", "odd": "1.90", "odd_float": 1.90},
        {"selection": "Under 2.25", "odd": "1.90", "odd_float": 1.90},
    ]

    lines = service._select_main_line_by_id(5, "Goals Over/Under", market_values)

    assert len(lines) == 2
    assert all("myanmar_odd" in item for item in lines)


def test_canonical_handicap_key_preserves_signs():
    service = OddsService(client=_DummyClient())

    assert service._canonical_handicap_key("-0.25", -0.25) == "-0.25"
    assert service._canonical_handicap_key("+0.25", 0.25) == "+0.25"
    assert service._canonical_handicap_key("-1.00", -1.0) == "-1"
    assert service._canonical_handicap_key("+1.00", 1.0) == "+1"


def test_build_handicap_pairs_keeps_signed_handicap_lines_separate():
    service = OddsService(client=_DummyClient())

    market_values = [
        {"selection": "Home -0.75", "odd": "1.85", "odd_float": 1.85},
        {"selection": "Home +0.75", "odd": "1.92", "odd_float": 1.92},
        {"selection": "Away +0.75", "odd": "1.94", "odd_float": 1.94},
        {"selection": "Away -0.75", "odd": "1.89", "odd_float": 1.89},
    ]

    pairs = service._build_handicap_pairs(market_values)

    assert set(pairs.keys()) == {"-0.75", "+0.75"}
    assert pairs["-0.75"]["home"]["selection"] == "Home -0.75"
    assert pairs["+0.75"]["home"]["selection"] == "Home +0.75"
    assert pairs["-0.75"]["away"]["selection"] == "Away -0.75"
    assert pairs["+0.75"]["away"]["selection"] == "Away +0.75"


def test_canonical_handicap_key_does_not_flip_away_signs():
    service = OddsService(client=_DummyClient())

    assert service._canonical_handicap_key("-0.75", -0.75, side="away") == "-0.75"
    assert service._canonical_handicap_key("+0.75", 0.75, side="away") == "+0.75"


def test_build_handicap_pairs_preserves_raw_api_same_sign_lines():
    service = OddsService(client=_DummyClient())

    market_values = [
        {"selection": "Home -0.75", "odd": "1.84", "odd_float": 1.84},
        {"selection": "Away -0.75", "odd": "1.95", "odd_float": 1.95},
        {"selection": "Home +0.75", "odd": "1.07", "odd_float": 1.07},
        {"selection": "Away +0.75", "odd": "8.25", "odd_float": 8.25},
    ]

    pairs = service._build_handicap_pairs(market_values)

    assert set(pairs.keys()) == {"-0.75", "+0.75"}
    assert pairs["-0.75"]["home"]["selection"] == "Home -0.75"
    assert pairs["-0.75"]["away"]["selection"] == "Away -0.75"
    assert pairs["+0.75"]["home"]["selection"] == "Home +0.75"
    assert pairs["+0.75"]["away"]["selection"] == "Away +0.75"


def test_select_main_line_by_id_uses_raw_handicap_lines_for_candidates():
    service = OddsService(client=_DummyClient())

    market_values = [
        {"selection": "Home -0.75", "odd": "1.84", "odd_float": 1.84},
        {"selection": "Away -0.75", "odd": "1.95", "odd_float": 1.95},
        {"selection": "Home +0.75", "odd": "1.07", "odd_float": 1.07},
        {"selection": "Away +0.75", "odd": "8.25", "odd_float": 8.25},
    ]

    lines = service._select_main_line_by_id(4, "Asian Handicap", market_values)

    assert len(lines) == 2
    assert [item["selection"] for item in lines] == ["Home -0.75", "Away -0.75"]


def test_goals_over_under_uses_fixed_orientation_even_with_favorite_team_override():
    service = OddsService(client=_DummyClient())
    myanmar_service = MyanmarOddsService()

    market_values = [
        {"selection": "Over 2.25", "odd": "1.81", "odd_float": 1.81},
        {"selection": "Under 2.25", "odd": "2.02", "odd_float": 2.02},
    ]

    lines = service._select_main_line_by_id(5, "Goals Over/Under", market_values, favorite_team="AWAY")

    label = myanmar_service.convert_to_myanmar_odds(1.81, 2.02, "2.25").market_label
    opposite_label = myanmar_service.other_side_label(label)

    over_line = next(item for item in lines if item["selection"].startswith("Over "))
    under_line = next(item for item in lines if item["selection"].startswith("Under "))

    assert over_line["myanmar_odd"] == label
    assert under_line["myanmar_odd"] == opposite_label

def test_filter_main_lines_keeps_myanmar_odd_null_for_unsupported_markets():
    service = OddsService(client=_DummyClient())

    bookmaker_data = {
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
                "id": 45,
                "name": "Corners Over/Under",
                "values": [
                    {"value": "Over 9.5", "odd": "2.00"},
                    {"value": "Under 9.5", "odd": "1.90"},
                ],
            },
        ],
    }

    filtered = service._filter_main_lines(bookmaker_data)

    assert all(item["myanmar_odd"] is None for item in filtered)
