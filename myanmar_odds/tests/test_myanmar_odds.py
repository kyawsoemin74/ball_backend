import pytest

from myanmar_odds.constants.base_index_map import get_base_index
from myanmar_odds.constants.ladder_map import ladder_label_for_index
from myanmar_odds.services.myanmar_odds_service import MyanmarOddsService
from myanmar_odds.utils.handicap_parser import parse_handicap_value


@pytest.mark.parametrize(
    ("handicap", "expected"),
    [
        (0.25, 50),
        (0.50, 100),
        (0.75, 150),
        (1.00, 200),
        (1.25, 250),
        (1.50, 300),
        (1.75, 350),
        (2.00, 400),
        (2.25, 450),
        (2.50, 500),
        (2.75, 550),
        (3.00, 600),
    ],
)
def test_base_index_map_examples(handicap, expected):
    assert get_base_index(handicap) == expected


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [
        ("Home -1.75", 1.75),
        ("Away +1.75", 1.75),
        ("Over 2.25", 2.25),
        ("Under 2.25", 2.25),
        ("-0.50", 0.50),
        ("+0.75", 0.75),
        (1.50, 1.50),
        (2.00, 2.00),
    ],
)
def test_handicap_parser_normalizes_values(raw_value, expected):
    assert parse_handicap_value(raw_value) == pytest.approx(expected)


@pytest.mark.parametrize(
    ("home_odds", "away_odds", "expected"),
    [
        (1.90, 1.90, 0),
        (1.80, 1.90, 10),
        (1.70, 1.90, 20),
        (1.60, 1.90, 30),
        (1.50, 1.90, 40),
        (2.00, 1.50, 50),
    ],
)
def test_diff_calculation(home_odds, away_odds, expected):
    service = MyanmarOddsService()
    assert service.calculate_diff(home_odds, away_odds) == expected


@pytest.mark.parametrize(
    ("home_odds", "away_odds", "expected"),
    [
        (1.70, 2.00, "HOME"),
        (2.00, 1.70, "AWAY"),
        (1.90, 1.90, "DRAW"),
        (1.85, 1.99, "HOME"),
        (1.99, 1.85, "AWAY"),
    ],
)
def test_market_side_detection(home_odds, away_odds, expected):
    service = MyanmarOddsService()
    assert service.detect_market_side(home_odds, away_odds) == expected


@pytest.mark.parametrize(
    ("index", "expected"),
    [
        (50, "-50"),
        (60, "-60"),
        (70, "-70"),
        (80, "-80"),
        (90, "-90"),
        (100, "-100"),
        (110, "1+90"),
        (120, "1+80"),
        (130, "1+70"),
        (200, "1="),
        (210, "1-10"),
        (300, "1-100"),
        (310, "2+90"),
        (400, "2="),
    ],
)
def test_ladder_lookup_examples(index, expected):
    assert ladder_label_for_index(index) == expected


def test_ladder_lookup_rejects_out_of_range_index():
    assert ladder_label_for_index(1) == "OUT_OF_RANGE"


@pytest.mark.parametrize(
    ("index", "expected"),
    [
        (10, "-10"),
        (20, "-20"),
        (30, "-30"),
        (40, "-40"),
        (50, "-50"),
        (60, "-60"),
        (70, "-70"),
        (80, "-80"),
        (90, "-90"),
        (100, "-100"),
    ],
)
def test_ladder_lookup_supports_lower_floor_values(index, expected):
    assert ladder_label_for_index(index) == expected


def test_index_shift_uses_positive_market_index_for_both_favorites():
    service = MyanmarOddsService()

    assert service.shift_index(base_index=100, diff=30, market_side="HOME") == 130
    assert service.shift_index(base_index=100, diff=30, market_side="AWAY") == 130
    assert service.shift_index(base_index=150, diff=27, market_side="HOME") == 177
    assert service.shift_index(base_index=150, diff=27, market_side="AWAY") == 177


def test_full_conversion_flow_home_favorite():
    service = MyanmarOddsService()
    result = service.convert_to_myanmar_odds(
        home_odds=1.70,
        away_odds=2.00,
        handicap="0.50",
    )

    assert result.home_odds == pytest.approx(1.70)
    assert result.away_odds == pytest.approx(2.00)
    assert result.market_side == "HOME"
    assert result.base_index == 100
    assert result.diff == 30
    assert result.market_index == 130
    assert result.market_label == ladder_label_for_index(130)
    assert result.status == "OK"


def test_convert_to_myanmar_odds_uses_explicit_favorite_team_override():
    service = MyanmarOddsService()

    result = service.convert_to_myanmar_odds(
        home_odds=2.00,
        away_odds=1.70,
        handicap="0.50",
        favorite_team="AWAY",
    )

    assert result.market_side == "AWAY"
    assert result.base_index == 100
    assert result.diff == 30
    assert result.market_index == 130
    assert result.market_label == ladder_label_for_index(130)


@pytest.mark.parametrize(
    ("favorite_team", "home_odds", "away_odds", "expected_supported", "expected_market_index"),
    [
        ("AWAY", 2.21, 1.66, True, 205),
        ("AWAY", 1.66, 2.21, False, 95),
    ],
)
def test_convert_to_myanmar_odds_direction_rule_uses_favorite_team_odds(
    favorite_team,
    home_odds,
    away_odds,
    expected_supported,
    expected_market_index,
):
    service = MyanmarOddsService()

    result = service.convert_to_myanmar_odds(
        home_odds=home_odds,
        away_odds=away_odds,
        handicap="-0.75",
        favorite_team=favorite_team,
    )

    assert result.base_index == 150
    assert result.diff == 55
    assert result.market_index == expected_market_index
    assert result.market_label == ladder_label_for_index(expected_market_index)

    favorite_odds = home_odds if favorite_team == "HOME" else away_odds
    other_odds = away_odds if favorite_team == "HOME" else home_odds
    favorite_is_supported = favorite_odds < other_odds

    assert favorite_is_supported is expected_supported


def test_convert_to_myanmar_odds_direction_rule_home_favorite_uses_addition():
    service = MyanmarOddsService()

    result = service.convert_to_myanmar_odds(
        home_odds=1.66,
        away_odds=2.21,
        handicap="0.75",
        favorite_team="HOME",
    )

    assert result.base_index == 150
    assert result.diff == 55
    assert result.market_index == 205
    assert result.market_label == ladder_label_for_index(205)


def test_convert_to_myanmar_odds_direction_rule_home_favorite_uses_subtraction():
    service = MyanmarOddsService()

    result = service.convert_to_myanmar_odds(
        home_odds=2.21,
        away_odds=1.66,
        handicap="0.75",
        favorite_team="HOME",
    )

    assert result.base_index == 150
    assert result.diff == 55
    assert result.market_index == 95
    assert result.market_label == ladder_label_for_index(95)


def test_market_label_comes_directly_from_market_index():
    assert ladder_label_for_index(120) == "1+80"
    assert ladder_label_for_index(177) == "1+20"
    assert ladder_label_for_index(220) == "1-20"


def test_other_side_label_flips_signs_without_mirror_index_logic():
    service = MyanmarOddsService()

    assert service.other_side_label("1+80") == "1-80"
    assert service.other_side_label("1-80") == "1+80"
    assert service.other_side_label("+50") == "-50"
    assert service.other_side_label("-50") == "+50"
    assert service.other_side_label("2=") == "2="
    assert service.other_side_label("D") == "D"


def test_boundary_case_250_handicap_uses_single_market_label():
    service = MyanmarOddsService()

    result = service.convert_to_myanmar_odds(
        home_odds=1.85,
        away_odds=1.84,
        handicap="2.50",
    )

    assert result.base_index == 500
    assert result.market_index == 501
    assert result.market_label == ladder_label_for_index(501)


def test_ladder_audit_from_10_to_5000_has_no_boundary_collapses():
    allowed_identical_labels = {"D"}

    for index in range(10, 5001):
        label = ladder_label_for_index(index)

        assert label != "OUT_OF_RANGE"
        assert "+100" not in label

        if label.endswith("="):
            allowed_identical_labels.add(label)

        if label not in allowed_identical_labels:
            assert ladder_label_for_index(index) == label


def test_service_uses_market_label_from_market_index():
    service = MyanmarOddsService()
    result = service.convert_to_myanmar_odds(1.80, 1.90, "1.00")

    assert result.base_index == 200
    assert result.market_label == ladder_label_for_index(result.market_index)
    assert result.market_label.startswith("1")


def test_service_handles_zero_diff():
    service = MyanmarOddsService()
    result = service.convert_to_myanmar_odds(1.90, 1.90, "0.25")

    assert result.diff == 0
    assert result.market_index == 50
    assert result.status == "OK"


@pytest.mark.parametrize(
    ("home_odds", "away_odds", "expected_market", "expected_mirror", "expected_status"),
    [
        (1.87, 1.95, "-8", "+8", "OK"),
        (1.80, 1.90, "-10", "+10", "OK"),
        (1.60, 1.90, "-30", "+30", "OK"),
    ],
)
def test_ah_zero_uses_direct_diff_rule(home_odds, away_odds, expected_market, expected_mirror, expected_status):
    service = MyanmarOddsService()

    result = service.convert_to_myanmar_odds(home_odds, away_odds, 0.00)

    assert result.base_index == 0
    assert result.diff == int(round(abs(home_odds - away_odds) * 100))
    assert result.market_side == "HOME"
    assert result.market_label == expected_market
    assert result.status == expected_status


def test_ah_zero_equal_odds_returns_d_label():
    service = MyanmarOddsService()

    result = service.convert_to_myanmar_odds(1.90, 1.90, 0.00)

    assert result.base_index == 0
    assert result.diff == 0
    assert result.market_side == "DRAW"
    assert result.market_label == "D"
    assert result.status == "OK"


def test_supported_handicap_cases_never_report_out_of_range():
    service = MyanmarOddsService()

    cases = [
        (0.25, 2.00, 1.70),
        (0.50, 2.00, 1.60),
        (0.75, 1.90, 1.50),
        (1.00, 1.90, 1.60),
        (2.25, 2.05, 1.65),
        (2.50, 2.10, 1.55),
    ]

    for handicap, home_odds, away_odds in cases:
        result = service.convert_to_myanmar_odds(home_odds, away_odds, handicap)
        assert result.status != "OUT_OF_RANGE"
        assert result.market_label != "OUT_OF_RANGE"


def test_service_uses_absolute_handicap_value():
    service = MyanmarOddsService()
    result = service.convert_to_myanmar_odds(1.70, 2.00, "-0.50")

    assert result.base_index == 100
    assert result.handicap == pytest.approx(0.50)


def test_service_raises_for_invalid_handicap():
    service = MyanmarOddsService()

    with pytest.raises(ValueError):
        service.convert_to_myanmar_odds(1.70, 2.00, "invalid")


def test_service_rejects_unsupported_handicap_values_with_clear_error():
    service = MyanmarOddsService()

    with pytest.raises(ValueError, match="Unsupported handicap|quarter"):
        service.convert_to_myanmar_odds(1.70, 2.00, "0.10")

    with pytest.raises(ValueError, match="Unsupported handicap|quarter"):
        service.convert_to_myanmar_odds(1.70, 2.00, "1.30")
