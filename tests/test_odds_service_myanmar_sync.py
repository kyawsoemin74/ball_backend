import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.services.odds_service import OddsService
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
