import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.odds_service import OddsService
from app.services.odds_sync_service import VALIDATION_FAILED, OddsSyncService
from myanmar_odds.services.myanmar_odds_service import MyanmarOddsService


class _DummyClient:
    pass


class _FakeClient:
    def __init__(self, payload=None):
        self.payload = payload or {"response": []}
        self.calls = 0

    async def get(self, _path, params=None):
        self.calls += 1
        return self.payload


class _FakeScalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeResult:
    def __init__(self, *, match=None, odds_rows=None):
        self._match = match
        self._odds_rows = odds_rows if odds_rows is not None else []

    def scalar_one_or_none(self):
        return self._match

    def scalars(self):
        return _FakeScalars(self._odds_rows)


class _FakeDB:
    def __init__(self, status="NS", odds_rows=None):
        self.match = SimpleNamespace(match_id=123, status=status)
        self.odds_rows = list(odds_rows or [])
        self.added = []
        self.commits = 0
        self.flushes = 0

    async def execute(self, query):
        query_text = str(query)
        if "FROM matches" in query_text:
            return _FakeResult(match=self.match)
        if "FROM odds" in query_text:
            return _FakeResult(odds_rows=self.odds_rows)
        if query_text.strip().startswith("DELETE FROM odds"):
            self.odds_rows = []
            return _FakeResult()
        return _FakeResult()

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        self.flushes += 1

    async def commit(self):
        self.commits += 1


class _FakeCacheService:
    def __init__(self, cached=None):
        self.cached = cached
        self.set_calls = []

    async def get_json(self, _key):
        return self.cached

    async def set_json(self, key, value, ttl):
        self.set_calls.append((key, value, ttl))


def _candidate_bookmaker(*bets, name="1xBet"):
    return {"id": 11, "name": name, "bets": list(bets)}


def _candidate_market(market_id, name, *values):
    return {
        "id": market_id,
        "name": name,
        "values": [{"value": selection, "odd": odd} for selection, odd in values],
    }


def _required_market_bets():
    return [
        _candidate_market(1, "Match Winner", ("Home", "1.80"), ("Draw", "3.20"), ("Away", "4.10")),
        _candidate_market(4, "Asian Handicap", ("Home -0.5", "1.91"), ("Away -0.5", "1.95")),
        _candidate_market(5, "Goals Over/Under", ("Over 2.5", "1.95"), ("Under 2.5", "1.90")),
    ]


def test_candidate_market_ids_normalize_and_null_myanmar_is_allowed_for_ids_1_and_45():
    service = OddsService(client=_DummyClient())
    candidate, diagnostics = service.build_candidate(
        _candidate_bookmaker(
            *_required_market_bets(),
            _candidate_market(45, "Corners Over/Under", ("Over 9.5", "2.00"), ("Under 9.5", "1.90")),
        )
    )

    assert diagnostics == []
    assert {row["market_name"] for row in candidate} == {
        "MATCH_WINNER",
        "ASIAN_HANDICAP",
        "GOALS_OVER_UNDER",
        "CORNERS_OVER_UNDER",
    }
    assert all(
        row["myanmar_odd"] is None
        for row in candidate
        if row["market_id"] in {1, 45}
    )


@pytest.mark.parametrize(
    ("home_odd", "away_odd", "expected_giving_team"),
    [
        ("1.90", "3.50", "HOME"),
        ("3.50", "1.90", "AWAY"),
        ("2.50", "2.50", None),
    ],
)
def test_candidate_derives_giving_team_and_preserves_raw_asian_handicap_values(
    home_odd,
    away_odd,
    expected_giving_team,
):
    service = OddsService(client=_DummyClient())
    bets = _required_market_bets()
    bets[0] = _candidate_market(
        1,
        "Match Winner",
        ("Home", home_odd),
        ("Draw", "3.20"),
        ("Away", away_odd),
    )
    bets[1] = _candidate_market(
        4,
        "Asian Handicap",
        (" Home -0.50 ", "1.9100"),
        ("Away -0.50", "1.950"),
    )

    candidate, _diagnostics = service.build_candidate(_candidate_bookmaker(*bets))

    handicap_rows = [row for row in candidate if row["market_name"] == "ASIAN_HANDICAP"]
    assert [row["selection"] for row in handicap_rows] == [" Home -0.50 ", "Away -0.50"]
    assert [row["odd_value"] for row in handicap_rows] == ["1.9100", "1.950"]
    assert {row["giving_team"] for row in handicap_rows} == {expected_giving_team}
    assert all(
        row["giving_team"] is None
        for row in candidate
        if row["market_name"] != "ASIAN_HANDICAP"
    )


@pytest.mark.parametrize(
    ("market_id", "name", "values", "category"),
    [
        (4, "Asian Handicap", (("Home -0.5", "1.91"), ("Away -0.5", "1.95")), "ASIAN_HANDICAP"),
        (5, "Goals Over/Under", (("Over 2.5", "1.95"), ("Under 2.5", "1.90")), "GOALS_OVER_UNDER"),
    ],
)
def test_candidate_myanmar_markets_require_complete_conversion(market_id, name, values, category):
    service = OddsService(client=_DummyClient())
    candidate, _diagnostics = service.build_candidate(
        _candidate_bookmaker(
            *[
                bet
                for bet in _required_market_bets()
                if bet["id"] != market_id
            ],
            _candidate_market(market_id, name, *values),
        )
    )

    converted_rows = [row for row in candidate if row["market_name"] == category]
    assert len(converted_rows) == 2
    assert all(
        isinstance(row["myanmar_odd"], str) and row["myanmar_odd"]
        for row in converted_rows
    )


def test_candidate_ignores_unknown_ids_with_complete_required_markets():
    service = OddsService(client=_DummyClient())
    source = _candidate_bookmaker(
        *_required_market_bets(),
        _candidate_market(45, "Corners Over/Under", ("Over 9.5", "2.00"), ("Under 9.5", "1.90")),
        _candidate_market(3, "Second Half Winner", ("invalid", "NaN")),
        _candidate_market(8, "Both Teams Score", ("invalid", "NaN")),
        _candidate_market(55, "Corners 1x2", ("invalid", "NaN")),
    )

    candidate, diagnostics = service.build_candidate(source)

    assert {row["market_id"] for row in candidate} == {1, 4, 5, 45}
    assert {row["market_name"] for row in candidate} == {
        "MATCH_WINNER",
        "ASIAN_HANDICAP",
        "GOALS_OVER_UNDER",
        "CORNERS_OVER_UNDER",
    }
    assert [item["market_id"] for item in diagnostics] == [3, 8, 55]
    assert all(item["reason"] == "UNKNOWN_MARKET" for item in diagnostics)


def test_candidate_accepts_missing_optional_corners_market():
    candidate, _diagnostics = OddsService(client=_DummyClient()).build_candidate(
        _candidate_bookmaker(*_required_market_bets())
    )

    assert {row["market_id"] for row in candidate} == {1, 4, 5}


def test_invalid_candidate_preserves_existing_odds_and_analytics_without_replacement():
    existing_odds = [{"market_name": "MATCH_WINNER", "selection": "Home"}]
    existing_analytics = [{"market_name": "MATCH_WINNER", "selection": "Home"}]
    odds_repository = SimpleNamespace(
        rows=existing_odds,
        replace_fixture_odds=AsyncMock(side_effect=AssertionError("Odds replacement must not run"))
    )
    analytics_repository = SimpleNamespace(
        rows=existing_analytics,
        replace_by_match=AsyncMock(side_effect=AssertionError("Analytics replacement must not run"))
    )
    analytics_service = SimpleNamespace(
        odds_repository=analytics_repository,
        project_odds=AsyncMock(side_effect=AssertionError("Analytics projection must not run")),
    )
    provider = SimpleNamespace(
        get_match_odds=AsyncMock(
            return_value={
                "response": [
                    {
                        "fixture": {"id": 123},
                        "bookmakers": [
                            {
                                "id": 11,
                                "name": "1xBet",
                                "bets": [
                                    _candidate_market(
                                        1,
                                        "Match Winner",
                                        ("Home", "1.80"),
                                        ("Draw", "3.20"),
                                        ("Away", "4.10"),
                                    ),
                                    _candidate_market(
                                        4,
                                        "Asian Handicap",
                                        ("Home -0.5", "1.91"),
                                        ("Away -0.5", "1.95"),
                                    ),
                                ],
                            }
                        ],
                    }
                ]
            }
        )
    )
    sync_service = OddsSyncService(
        OddsService(client=_DummyClient()),
        odds_provider=provider,
        odds_repository=odds_repository,
        analytics_projection_service=analytics_service,
    )
    db = SimpleNamespace(
        flush=AsyncMock(),
        scalar=AsyncMock(
            return_value=SimpleNamespace(
                local_match_id=42,
                provider="api-football",
                provider_fixture_id=123,
            )
        ),
    )

    result = asyncio.run(
        sync_service.refresh_odds(
            db,
            123,
            "odds:test",
            1800,
            local_match_id=42,
        )
    )

    assert result["status"] == VALIDATION_FAILED
    assert result["reason"] == "MISSING_REQUIRED_MARKETS"
    assert odds_repository.rows == [{"market_name": "MATCH_WINNER", "selection": "Home"}]
    assert analytics_repository.rows == [{"market_name": "MATCH_WINNER", "selection": "Home"}]
    odds_repository.replace_fixture_odds.assert_not_awaited()
    analytics_repository.replace_by_match.assert_not_awaited()
    analytics_service.project_odds.assert_not_awaited()


@pytest.mark.parametrize("missing_market_id", [1, 4, 5])
def test_candidate_rejects_each_missing_required_market(missing_market_id):
    source = _candidate_bookmaker(
        *[
            bet
            for bet in _required_market_bets()
            if bet["id"] != missing_market_id
        ]
    )

    with pytest.raises(ValueError) as error:
        OddsService(client=_DummyClient()).build_candidate(source)

    assert error.value.reason == "MISSING_REQUIRED_MARKETS"
    assert {
        item["market_id"]
        for item in error.value.diagnostics
        if item["reason"] == "REQUIRED_MARKET_MISSING"
    } == {missing_market_id}


@pytest.mark.parametrize(
    ("source", "reason"),
    [
        (_candidate_bookmaker(_candidate_market(700, "Unknown", ("Option A", "2.00"))), "NO_SUPPORTED_MARKETS"),
        (
            _candidate_bookmaker(
                _candidate_market(1, "Match Winner", ("Home", "1.80"), ("Away", "4.10")),
                *_required_market_bets()[1:],
            ),
            "INCOMPLETE_MATCH_WINNER",
        ),
        (
            _candidate_bookmaker(
                _candidate_market(1, "Match Winner", ("Home", "1.80"), ("Draw", "3.20"), ("Away", "NaN")),
                *_required_market_bets()[1:],
            ),
            "INVALID_ODD",
        ),
        (
            _candidate_bookmaker(
                *_required_market_bets(),
                _candidate_market(45, "Corners", ("Over 9.5", "2.00"), ("Under 10.5", "2.00")),
            ),
            "INCOMPLETE_CORNERS_OVER_UNDER",
        ),
        (
            _candidate_bookmaker(
                _candidate_market(1, "Match Winner", ("Home", "1.80"), ("Draw", "3.20"), ("Away", "4.10")),
                _candidate_market(1, "Duplicate", ("Home", "1.80"), ("Draw", "3.20"), ("Away", "4.10")),
            ),
            "DUPLICATE_MARKET_ID",
        ),
    ],
)
def test_candidate_rejects_incomplete_ambiguous_or_invalid_market(source, reason):
    service = OddsService(client=_DummyClient())

    with pytest.raises(ValueError) as error:
        service.build_candidate(source)

    assert error.value.reason == reason


def test_candidate_requires_bookmaker_name_to_match_approved_identity():
    service = OddsService(client=_DummyClient())
    source = _candidate_bookmaker(
        _candidate_market(1, "Match Winner", ("Home", "1.80"), ("Draw", "3.20"), ("Away", "4.10")),
        name="Different bookmaker",
    )

    with pytest.raises(ValueError) as error:
        service.build_candidate(source)

    assert error.value.reason == "BOOKMAKER_NAME_MISMATCH"


def _complete_snapshot(updated_at=None):
    updated_at = updated_at or datetime.now(timezone.utc)
    return [
        SimpleNamespace(
            bookmaker_name="1xBet",
            market_name=market_name,
            selection=selection,
            odd_value=odd,
            myanmar_odd=myanmar_odd,
            last_updated=updated_at,
        )
        for market_name, market_rows in (
            ("MATCH_WINNER", [("Home", "1.8", None), ("Draw", "3.2", None), ("Away", "4.1", None)]),
            ("ASIAN_HANDICAP", [("Home -0.5", "1.91", "HOME"), ("Away -0.5", "1.95", "AWAY")]),
            ("GOALS_OVER_UNDER", [("Over 2.5", "1.95", "OVER"), ("Under 2.5", "1.90", "UNDER")]),
        )
        for selection, odd, myanmar_odd in market_rows
    ]


def test_freshness_snapshot_must_be_complete_and_share_one_timestamp():
    service = OddsService(client=_DummyClient())
    updated_at = datetime.now(timezone.utc)
    complete = _complete_snapshot(updated_at)

    assert service.is_valid_snapshot(complete)
    assert service.is_valid_snapshot(
        complete
        + [
            SimpleNamespace(
                bookmaker_name="1xBet",
                market_name="CORNERS_OVER_UNDER",
                selection=selection,
                odd_value=odd,
                myanmar_odd=None,
                last_updated=updated_at,
            )
            for selection, odd in (("Over 9.5", "2.00"), ("Under 9.5", "1.90"))
        ]
    )
    assert not service.is_valid_snapshot(complete[:3])
    assert not service.is_valid_snapshot(
        [row for row in complete if row.market_name != "ASIAN_HANDICAP"]
    )
    complete[0].last_updated = datetime.now(timezone.utc)
    assert not service.is_valid_snapshot(complete)


def test_freshness_validation_normalizes_ah_selection_without_rewriting_it():
    service = OddsService(client=_DummyClient())
    snapshot = _complete_snapshot()
    snapshot[3].selection = " Home -0.50 "
    original_selection = snapshot[3].selection

    assert service.is_valid_snapshot(snapshot)
    assert snapshot[3].selection == original_selection


@pytest.mark.parametrize(
    "odd_value",
    [
        "NaN",
        "Infinity",
        "-Infinity",
        "not-a-number",
        "0",
        "-1",
        Decimal("NaN"),
        Decimal("sNaN"),
        Decimal("Infinity"),
        Decimal("-Infinity"),
    ],
)
def test_stored_snapshot_rejects_invalid_odd_values(odd_value):
    service = OddsService(client=_DummyClient())
    snapshot = _complete_snapshot()
    snapshot[0].odd_value = odd_value

    assert not service.is_valid_snapshot(snapshot)


@pytest.mark.parametrize(
    ("market_name", "selections"),
    [
        ("ASIAN_HANDICAP", ("Home NaN", "Away NaN")),
        ("ASIAN_HANDICAP", ("Home Infinity", "Away Infinity")),
        ("ASIAN_HANDICAP", ("Home -Infinity", "Away -Infinity")),
        ("GOALS_OVER_UNDER", ("Over NaN", "Under NaN")),
        ("GOALS_OVER_UNDER", ("Over Infinity", "Under Infinity")),
        ("GOALS_OVER_UNDER", ("Over -Infinity", "Under -Infinity")),
        ("CORNERS_OVER_UNDER", ("Over Infinity", "Under Infinity")),
        ("ASIAN_HANDICAP", ("Home malformed", "Away malformed")),
        ("GOALS_OVER_UNDER", ("Over malformed", "Under malformed")),
        ("ASIAN_HANDICAP", ("Home ", "Away ")),
        ("GOALS_OVER_UNDER", ("Over ", "Under ")),
    ],
)
def test_stored_snapshot_rejects_nonfinite_or_malformed_market_lines(market_name, selections):
    service = OddsService(client=_DummyClient())
    snapshot = [
        row for row in _complete_snapshot()
        if row.market_name != market_name
    ]
    myanmar_odd = "LINE" if market_name in {"ASIAN_HANDICAP", "GOALS_OVER_UNDER"} else None
    snapshot.extend(
        SimpleNamespace(
            bookmaker_name="1xBet",
            market_name=market_name,
            selection=selection,
            odd_value="1.90",
            myanmar_odd=myanmar_odd,
            last_updated=snapshot[0].last_updated,
        )
        for selection in selections
    )

    assert not service.is_valid_snapshot(snapshot)


@pytest.mark.parametrize(
    ("market_name", "selections"),
    [
        ("ASIAN_HANDICAP", ("Home -0.750", "Away -0.75")),
        ("GOALS_OVER_UNDER", ("Over 2.50", "Under 2.5")),
    ],
)
def test_stored_snapshot_accepts_negative_handicap_and_equivalent_lines(market_name, selections):
    service = OddsService(client=_DummyClient())
    snapshot = _complete_snapshot()
    market_rows = [row for row in snapshot if row.market_name == market_name]
    market_rows[0].selection, market_rows[1].selection = selections

    assert service.is_valid_snapshot(snapshot)


def test_recent_malformed_snapshot_is_not_valid_for_freshness_skip():
    service = OddsService(client=_DummyClient())
    updated_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    snapshot = _complete_snapshot(updated_at)
    snapshot[0].odd_value = "Infinity"

    snapshot_is_fresh = (
        datetime.now(timezone.utc) - updated_at < timedelta(hours=12)
    )

    assert snapshot_is_fresh
    assert not (service.is_valid_snapshot(snapshot) and snapshot_is_fresh)


def _db_odd(updated_at):
    return SimpleNamespace(
        bookmaker_name="1xBet",
        market_name="Match Winner",
        selection="Home",
        odd_value="1.80",
        myanmar_odd=None,
        last_updated=updated_at,
    )


def _api_payload(fixture_id=123):
    return {
        "response": [
            {
                "fixture": {"id": fixture_id},
                "bookmakers": [
                    {
                        "id": 11,
                        "name": "1xBet",
                        "bets": [
                            {
                                "id": 1,
                                "name": "Match Winner",
                                "values": [
                                    {"value": "Home", "odd": "1.80"},
                                    {"value": "Draw", "odd": "3.30"},
                                    {"value": "Away", "odd": "4.50"},
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
                ],
            }
        ]
    }


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

def test_filter_main_lines_keeps_myanmar_odd_null_for_noneligible_markets():
    service = OddsService(client=_DummyClient())

    bookmaker_data = {
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

    assert all(
        item["myanmar_odd"] is None
        for item in filtered
        if item["market_id"] in {1, 45}
    )
    assert all(
        item["myanmar_odd"]
        for item in filtered
        if item["market_id"] in {4, 5}
    )


def test_get_cached_odds_ns_db_fresh_uses_db_and_ttl_1800():
    now = datetime.now(timezone.utc)
    db = _FakeDB(status="NS", odds_rows=[_db_odd(now - timedelta(minutes=10))])
    cache = _FakeCacheService(cached=None)
    client = _FakeClient(payload=_api_payload())
    service = OddsService(client=client, cache_service=cache)

    result = asyncio.run(service.get_cached_odds(db, 123))

    assert result["source"] == "database"
    assert client.calls == 0
    assert cache.set_calls == []


def test_get_cached_odds_ns_db_stale_uses_db_snapshot_and_ttl_1800():
    now = datetime.now(timezone.utc)
    db = _FakeDB(status="NS", odds_rows=[_db_odd(now - timedelta(minutes=45))])
    cache = _FakeCacheService(cached=None)
    client = _FakeClient(payload=_api_payload())
    service = OddsService(client=client, cache_service=cache)

    result = asyncio.run(service.get_cached_odds(db, 123))

    assert result["source"] == "database"
    assert client.calls == 0
    assert db.flushes == 0
    assert cache.set_calls == []
    assert result["cached"] is True
    assert result["match_started"] is False


def test_get_cached_odds_ns_db_empty_uses_db_snapshot_and_ttl_1800():
    db = _FakeDB(status="NS", odds_rows=[])
    cache = _FakeCacheService(cached=None)
    client = _FakeClient(payload=_api_payload())
    service = OddsService(client=client, cache_service=cache)

    result = asyncio.run(service.get_cached_odds(db, 123))

    assert result["source"] == "database"
    assert client.calls == 0
    assert db.flushes == 0
    assert cache.set_calls == []
    assert result["cached"] is True
    assert result["odds"] == []


def test_get_cached_odds_live_redis_miss_db_exists_uses_db_ttl_86400():
    now = datetime.now(timezone.utc)
    db = _FakeDB(status="LIVE", odds_rows=[_db_odd(now - timedelta(days=3))])
    cache = _FakeCacheService(cached=None)
    client = _FakeClient(payload=_api_payload())
    service = OddsService(client=client, cache_service=cache)

    result = asyncio.run(service.get_cached_odds(db, 123))

    assert result["source"] == "database"
    assert client.calls == 0
    assert cache.set_calls == []


def test_get_cached_odds_ft_redis_miss_db_exists_uses_db_ttl_86400():
    now = datetime.now(timezone.utc)
    db = _FakeDB(status="FT", odds_rows=[_db_odd(now - timedelta(days=2))])
    cache = _FakeCacheService(cached=None)
    client = _FakeClient(payload=_api_payload())
    service = OddsService(client=client, cache_service=cache)

    result = asyncio.run(service.get_cached_odds(db, 123))

    assert result["source"] == "database"
    assert client.calls == 0
    assert cache.set_calls == []


def test_get_cached_odds_live_db_empty_returns_empty_without_api():
    db = _FakeDB(status="LIVE", odds_rows=[])
    cache = _FakeCacheService(cached=None)
    client = _FakeClient(payload=_api_payload())
    service = OddsService(client=client, cache_service=cache)

    result = asyncio.run(service.get_cached_odds(db, 123))

    assert result["source"] == "database"
    assert result["odds"] == []
    assert client.calls == 0
    assert cache.set_calls == []


def test_get_cached_odds_started_status_never_calls_api():
    now = datetime.now(timezone.utc)
    db = _FakeDB(status="LIVE", odds_rows=[_db_odd(now - timedelta(days=1))])
    cache = _FakeCacheService(cached=None)
    client = _FakeClient(payload=_api_payload())
    service = OddsService(client=client, cache_service=cache)

    _ = asyncio.run(service.get_cached_odds(db, 123))

    assert client.calls == 0


def test_get_cached_odds_ns_sets_ttl_1800():
    now = datetime.now(timezone.utc)
    db = _FakeDB(status="NS", odds_rows=[_db_odd(now - timedelta(minutes=10))])
    cache = _FakeCacheService(cached=None)
    client = _FakeClient(payload=_api_payload())
    service = OddsService(client=client, cache_service=cache)

    _ = asyncio.run(service.get_cached_odds(db, 123))

    assert cache.set_calls == []


def test_get_cached_odds_live_ft_set_ttl_86400():
    now = datetime.now(timezone.utc)

    db_live = _FakeDB(status="LIVE", odds_rows=[_db_odd(now - timedelta(days=1))])
    cache_live = _FakeCacheService(cached=None)
    client_live = _FakeClient(payload=_api_payload())
    service_live = OddsService(client=client_live, cache_service=cache_live)
    _ = asyncio.run(service_live.get_cached_odds(db_live, 123))

    db_ft = _FakeDB(status="FT", odds_rows=[_db_odd(now - timedelta(days=1))])
    cache_ft = _FakeCacheService(cached=None)
    client_ft = _FakeClient(payload=_api_payload())
    service_ft = OddsService(client=client_ft, cache_service=cache_ft)
    _ = asyncio.run(service_ft.get_cached_odds(db_ft, 123))

    assert cache_live.set_calls == []
    assert cache_ft.set_calls == []


def test_date_matches_batch_stored_asian_handicap_odds_by_local_match_id(monkeypatch):
    from datetime import date

    from app.api import matches as matches_api

    matches = [
        SimpleNamespace(
            match_id=1510,
            provider_fixture_id=91010,
            league_id=2,
            match_time="2026-06-10T18:00:00+00:00",
            status="NS",
            home_team="Home A",
            away_team="Away A",
            home_score=0,
            away_score=0,
        ),
        SimpleNamespace(
            match_id=1511,
            provider_fixture_id=91011,
            league_id=2,
            match_time="2026-06-10T19:00:00+00:00",
            status="NS",
            home_team="Home B",
            away_team="Away B",
            home_score=0,
            away_score=0,
        ),
        SimpleNamespace(
            match_id=1512,
            provider_fixture_id=91012,
            league_id=2,
            match_time="2026-06-10T20:00:00+00:00",
            status="NS",
            home_team="Home C",
            away_team="Away C",
            home_score=0,
            away_score=0,
        ),
    ]
    stored_odds = [
        SimpleNamespace(fixture_id=1510, selection="Home -0.25", myanmar_odd="  -50 "),
        SimpleNamespace(fixture_id=1510, selection="Away -0.25", myanmar_odd=None),
        SimpleNamespace(fixture_id=1511, selection="Home +0.5", myanmar_odd="+25"),
    ]
    batch_calls = []

    async def get_allowed_ids(_self, _db):
        return {2}

    async def get_matches_by_date(_self, _db, _date, allowed_ids=None):
        assert allowed_ids == {2}
        return matches

    async def get_asian_handicap_for_fixtures(_self, _db, fixture_ids):
        batch_calls.append(fixture_ids)
        return stored_odds

    def preserve_order(rows):
        return rows

    async def unexpected_provider_call(*_args, **_kwargs):
        raise AssertionError("GET date endpoint must not call the provider")

    async def unexpected_sync_call(*_args, **_kwargs):
        raise AssertionError("GET date endpoint must not trigger Odds synchronization")

    monkeypatch.setattr(matches_api.AllowedLeagueRepository, "get_allowed_ids", get_allowed_ids)
    monkeypatch.setattr(matches_api.MatchRepository, "get_matches_by_date", get_matches_by_date)
    monkeypatch.setattr(matches_api.MatchRepository, "order_matches_for_date", staticmethod(preserve_order))
    monkeypatch.setattr(
        matches_api.OddsRepository,
        "get_asian_handicap_for_fixtures",
        get_asian_handicap_for_fixtures,
    )
    monkeypatch.setattr(matches_api.football_service, "get_match_odds", unexpected_provider_call)
    monkeypatch.setattr(matches_api.football_service.odds_sync_service, "refresh_odds", unexpected_sync_call)

    response = asyncio.run(matches_api.get_matches_by_date(date(2026, 6, 10), object()))
    payload = [match.model_dump(mode="json") for match in response]

    assert batch_calls == [[1510, 1511, 1512]]
    assert [match["match_id"] for match in payload] == [1510, 1511, 1512]
    assert payload[0]["home_team"] == "Home A"
    assert payload[0]["asian_handicap_odds"] == [
        {"selection": "Home -0.25", "myanmar_odd": "  -50 ", "giving_team": None},
        {"selection": "Away -0.25", "myanmar_odd": None, "giving_team": None},
    ]
    assert payload[1]["asian_handicap_odds"] == [
        {"selection": "Home +0.5", "myanmar_odd": "+25", "giving_team": None},
    ]
    assert payload[2]["asian_handicap_odds"] == []
    assert all(
        set(odd) == {"selection", "myanmar_odd", "giving_team"}
        for match in payload
        for odd in match["asian_handicap_odds"]
    )


def test_date_matches_without_matches_skip_odds_batch_query(monkeypatch):
    from datetime import date

    from app.api import matches as matches_api

    async def get_allowed_ids(_self, _db):
        return {2}

    async def get_matches_by_date(_self, _db, _date, allowed_ids=None):
        return []

    async def unexpected_odds_query(*_args, **_kwargs):
        raise AssertionError("Odds query must be skipped when no Matches were found")

    monkeypatch.setattr(matches_api.AllowedLeagueRepository, "get_allowed_ids", get_allowed_ids)
    monkeypatch.setattr(matches_api.MatchRepository, "get_matches_by_date", get_matches_by_date)
    monkeypatch.setattr(
        matches_api.OddsRepository,
        "get_asian_handicap_for_fixtures",
        unexpected_odds_query,
    )

    response = asyncio.run(matches_api.get_matches_by_date(date(2026, 6, 10), object()))

    assert response == []


def test_date_matches_odds_query_failure_propagates(monkeypatch):
    from datetime import date

    from app.api import matches as matches_api

    match = SimpleNamespace(
        match_id=1510,
        league_id=2,
        match_time="2026-06-10T18:00:00+00:00",
        status="NS",
        home_team="Home",
        away_team="Away",
        home_score=0,
        away_score=0,
    )

    async def get_allowed_ids(_self, _db):
        return {2}

    async def get_matches_by_date(_self, _db, _date, allowed_ids=None):
        return [match]

    async def failing_odds_query(_self, _db, fixture_ids):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(matches_api.AllowedLeagueRepository, "get_allowed_ids", get_allowed_ids)
    monkeypatch.setattr(matches_api.MatchRepository, "get_matches_by_date", get_matches_by_date)
    monkeypatch.setattr(
        matches_api.OddsRepository,
        "get_asian_handicap_for_fixtures",
        failing_odds_query,
    )

    with pytest.raises(RuntimeError, match="database unavailable"):
        asyncio.run(matches_api.get_matches_by_date(date(2026, 6, 10), object()))


def test_odds_repository_batches_local_ids_and_filters_asian_handicap():
    from app.repositories.odds_repository import OddsRepository

    rows = [SimpleNamespace(fixture_id=1510, market_name="ASIAN_HANDICAP")]

    class CapturingDB:
        queries = []

        async def execute(self, query):
            self.queries.append(query)
            return _FakeResult(odds_rows=rows)

    db = CapturingDB()
    result = asyncio.run(OddsRepository().get_asian_handicap_for_fixtures(db, [1510, 1511]))

    assert result == rows
    assert len(db.queries) == 1
    query = db.queries[0]
    assert "odds.fixture_id IN" in str(query)
    assert "odds.market_name =" in str(query)
    params = query.compile().params
    assert [value for value in params.values() if isinstance(value, list)] == [[1510, 1511]]
    assert "ASIAN_HANDICAP" in params.values()


def test_odds_repository_empty_batch_skips_database_query():
    from app.repositories.odds_repository import OddsRepository

    class CapturingDB:
        queries = []

        async def execute(self, query):
            self.queries.append(query)
            return _FakeResult(odds_rows=[])

    db = CapturingDB()

    result = asyncio.run(OddsRepository().get_asian_handicap_for_fixtures(db, []))

    assert result == []
    assert db.queries == []


def test_cached_odds_legacy_payload_without_giving_team_falls_back_to_database():
    cached = {
        "source": "database",
        "odds": [{"selection": "Home", "odd": "1.80"}],
        "cached": True,
        "match_started": False,
    }
    db = _FakeDB(
        status="NS",
        odds_rows=[
            SimpleNamespace(
                bookmaker_name="1xBet",
                market_name="ASIAN_HANDICAP",
                selection="Home -0.25",
                odd_value="1.9100",
                myanmar_odd="-90",
                giving_team="HOME",
                last_updated=datetime.now(timezone.utc),
            )
        ],
    )
    service = OddsService(
        client=_FakeClient(),
        cache_service=_FakeCacheService(cached=cached),
    )

    result = asyncio.run(service.get_cached_odds(db, 123))

    assert result["source"] == "database"
    assert result["odds"][0]["giving_team"] == "HOME"
    assert result["odds"][0]["odd"] == "1.9100"
