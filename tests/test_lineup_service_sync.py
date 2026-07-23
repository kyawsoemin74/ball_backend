import asyncio
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from app.core.config import settings
from app.models.match_lineup import MatchLineup
from app.services.lineup_service import LineupService, make_lineup_cache_key


class FakeClient:
    def __init__(self, responses: List[Dict[str, Any]]):
        self._responses = list(responses)
        self.calls = 0

    async def get(self, path: str, params: Dict[str, Any]) -> Dict[str, Any]:
        self.calls += 1
        return self._responses.pop(0)


class FakeScalarResult:
    def __init__(self, rows: List[MatchLineup]):
        self._rows = rows

    def scalar_one_or_none(self):
        if not self._rows:
            return None
        if len(self._rows) > 1:
            raise RuntimeError("Multiple rows found")
        return self._rows[0]


class FakeCacheService:
    def __init__(self):
        self.store: Dict[str, Any] = {}
        self.get_calls: List[str] = []
        self.set_calls: List[tuple[str, Any, int]] = []
        self.delete_calls: List[str] = []

    async def get_json(self, key: str) -> Any:
        self.get_calls.append(key)
        return self.store.get(key)

    async def set_json(self, key: str, value: Any, ttl: int) -> None:
        self.store[key] = value
        self.set_calls.append((key, value, ttl))

    async def delete(self, key: str) -> None:
        self.delete_calls.append(key)
        if key in self.store:
            del self.store[key]


class FakeTeamService:
    def __init__(self, squad_payloads: Dict[int, Dict[str, Any]]):
        self.squad_payloads = squad_payloads
        self.calls: List[int] = []

    async def get_cached_team_squad(self, team_id: int) -> Dict[str, Any] | None:
        self.calls.append(team_id)
        return self.squad_payloads.get(team_id)


class FakeLineupSyncService:
    def __init__(self, result: Dict[str, Any]):
        self.result = result
        self.calls: List[tuple[Any, int, Any, str, Any]] = []

    async def sync_lineup(self, db, match_id: int, *, cache_service, cache_key, validate_lineup) -> Dict[str, Any]:
        self.calls.append((db, match_id, cache_service, cache_key, validate_lineup))
        return self.result


class FakeSession:
    def __init__(self, statuses: Dict[int, str] | None = None):
        self.records: Dict[int, List[MatchLineup]] = {}
        self._pending: List[MatchLineup] = []
        self.statuses = statuses or {}

    async def execute(self, statement):
        model_name = self._extract_model_name(statement)
        match_id = self._extract_match_id(statement)

        if model_name == "Match":
            status = self.statuses.get(match_id)
            row = SimpleNamespace(match_id=match_id, status=status) if status is not None else None
            return FakeScalarResult([row] if row else [])

        rows = self.records.get(match_id, []) if match_id is not None else []
        return FakeScalarResult(rows)

    def add(self, row: MatchLineup):
        self._pending.append(row)

    async def flush(self):
        for row in self._pending:
            self.records.setdefault(row.match_id, []).append(row)
        self._pending.clear()

    async def commit(self):
        for row in self._pending:
            self.records.setdefault(row.match_id, []).append(row)
        self._pending.clear()

    async def rollback(self):
        self._pending.clear()

    @staticmethod
    def _extract_match_id(statement) -> int | None:
        where_criteria = list(getattr(statement, "_where_criteria", []))
        if not where_criteria:
            return None

        right = getattr(where_criteria[0], "right", None)
        return getattr(right, "value", None)

    @staticmethod
    def _extract_model_name(statement) -> str | None:
        descriptions = getattr(statement, "column_descriptions", [])
        if not descriptions:
            return None
        entity = descriptions[0].get("entity")
        if entity is None:
            return None
        return getattr(entity, "__name__", None)


def _valid_lineup(team_id: int, coach_name: str) -> Dict[str, Any]:
    return {
        "response": [
            {
                "team": {"id": team_id, "name": "Example FC"},
                "formation": "4-3-3",
                "startXI": [{"player": {"id": 10, "name": "Starter"}}],
                "substitutes": [{"player": {"id": 20, "name": "Sub"}}],
                "coach": {"id": 1, "name": coach_name},
            }
        ]
    }


def test_sync_lineup_delegates_to_sync_service():
    db = FakeSession()
    cache = FakeCacheService()
    expected = {"success": True, "match_id": 123, "created": False, "updated": True}
    sync_service = FakeLineupSyncService(expected)
    service = LineupService(
        client=FakeClient([]),
        cache_service=cache,
        lineup_sync_service=sync_service,
    )

    result = asyncio.run(service.sync_lineup(db, 123))

    assert result == expected
    assert len(sync_service.calls) == 1
    delegated_db, delegated_match_id, delegated_cache_service, delegated_cache_key, delegated_validator = sync_service.calls[0]
    assert delegated_db is db
    assert delegated_match_id == 123
    assert delegated_cache_service is cache
    assert delegated_cache_key == make_lineup_cache_key(123)
    assert delegated_validator([{"team": {"id": 1}, "startXI": [], "substitutes": []}]) is True
    assert delegated_validator([{"team": {"id": 1}, "startXI": "bad", "substitutes": []}]) is False


def test_sync_lineup_creates_new_row():
    db = FakeSession()
    cache = FakeCacheService()
    service = LineupService(client=FakeClient([_valid_lineup(1, "Coach A")]), cache_service=cache)

    result = asyncio.run(service.sync_lineup(db, 123))

    assert result == {"success": True, "match_id": 123, "created": True, "updated": False}
    assert 123 in db.records
    assert len(db.records[123]) == 1
    assert cache.delete_calls == [make_lineup_cache_key(123)]


def test_sync_lineup_updates_existing_row():
    db = FakeSession()
    existing = MatchLineup(match_id=123, data=_valid_lineup(1, "Old Coach")["response"])
    db.records[123] = [existing]

    cache = FakeCacheService()
    service = LineupService(client=FakeClient([_valid_lineup(1, "New Coach")]), cache_service=cache)
    result = asyncio.run(service.sync_lineup(db, 123))

    assert result == {"success": True, "match_id": 123, "created": False, "updated": True}
    assert len(db.records[123]) == 1
    assert db.records[123][0].data[0]["coach"]["name"] == "New Coach"
    assert cache.delete_calls == [make_lineup_cache_key(123)]


def test_sync_lineup_returns_not_available_on_empty_response():
    db = FakeSession()
    service = LineupService(client=FakeClient([{"response": []}]), cache_service=FakeCacheService())

    result = asyncio.run(service.sync_lineup(db, 123))

    assert result == {"success": False, "match_id": 123, "reason": "lineup_not_available"}
    assert 123 not in db.records


def test_sync_lineup_returns_not_available_on_invalid_structure():
    db = FakeSession()
    invalid_payload = {"response": [{"team": {"id": 1}, "startXI": "bad", "substitutes": []}]}
    service = LineupService(client=FakeClient([invalid_payload]), cache_service=FakeCacheService())

    result = asyncio.run(service.sync_lineup(db, 123))

    assert result == {"success": False, "match_id": 123, "reason": "lineup_not_available"}
    assert 123 not in db.records


def test_sync_lineup_duplicate_protection_on_repeated_syncs():
    db = FakeSession()
    cache = FakeCacheService()
    service = LineupService(
        client=FakeClient([
            _valid_lineup(1, "Coach A"),
            _valid_lineup(1, "Coach B"),
        ]),
        cache_service=cache,
    )

    first = asyncio.run(service.sync_lineup(db, 123))
    second = asyncio.run(service.sync_lineup(db, 123))

    assert first == {"success": True, "match_id": 123, "created": True, "updated": False}
    assert second == {"success": True, "match_id": 123, "created": False, "updated": True}
    assert len(db.records[123]) == 1
    assert db.records[123][0].data[0]["coach"]["name"] == "Coach B"
    assert cache.delete_calls == [make_lineup_cache_key(123), make_lineup_cache_key(123)]


def test_sync_lineup_status_gate_ns_allows_sync():
    client = FakeClient([_valid_lineup(1, "Coach NS")])
    db = FakeSession(statuses={123: "NS"})
    service = LineupService(client=client, cache_service=FakeCacheService())

    result = asyncio.run(service.sync_lineup(db, 123))

    assert result == {"success": True, "match_id": 123, "created": True, "updated": False}
    assert client.calls == 1


def test_sync_lineup_status_gate_live_allows_sync():
    client = FakeClient([_valid_lineup(1, "Coach LIVE")])
    db = FakeSession(statuses={123: "LIVE"})
    service = LineupService(client=client, cache_service=FakeCacheService())

    result = asyncio.run(service.sync_lineup(db, 123))

    assert result == {"success": True, "match_id": 123, "created": True, "updated": False}
    assert client.calls == 1


def test_sync_lineup_status_gate_ft_is_skipped_without_api_call():
    client = FakeClient([])
    db = FakeSession(statuses={123: "FT"})
    cache = FakeCacheService()
    service = LineupService(client=client, cache_service=cache)

    result = asyncio.run(service.sync_lineup(db, 123))

    assert result == {
        "success": True,
        "match_id": 123,
        "skipped": True,
        "reason": "status_blocked",
        "status": "FT",
    }
    assert client.calls == 0
    assert 123 not in db.records
    assert cache.delete_calls == []


def test_sync_lineup_status_gate_pst_is_skipped_without_api_call():
    client = FakeClient([])
    db = FakeSession(statuses={123: "PST"})
    cache = FakeCacheService()
    service = LineupService(client=client, cache_service=cache)

    result = asyncio.run(service.sync_lineup(db, 123))

    assert result == {
        "success": True,
        "match_id": 123,
        "skipped": True,
        "reason": "status_blocked",
        "status": "PST",
    }
    assert client.calls == 0
    assert 123 not in db.records
    assert cache.delete_calls == []


def test_sync_lineup_status_gate_canc_is_skipped_without_api_call():
    client = FakeClient([])
    db = FakeSession(statuses={123: "CANC"})
    cache = FakeCacheService()
    service = LineupService(client=client, cache_service=cache)

    result = asyncio.run(service.sync_lineup(db, 123))

    assert result == {
        "success": True,
        "match_id": 123,
        "skipped": True,
        "reason": "status_blocked",
        "status": "CANC",
    }
    assert client.calls == 0
    assert 123 not in db.records
    assert cache.delete_calls == []


def test_get_cached_match_lineup_enriches_players_with_squad_photos():
    client = FakeClient([])
    cache = FakeCacheService()
    db = FakeSession()
    db.records[123] = [
        MatchLineup(
            match_id=123,
            data=[
                {
                    "team": {"id": 1, "name": "Home FC"},
                    "startXI": [{"player": {"id": 10, "name": "Starter"}}],
                    "substitutes": [{"player": {"id": 20, "name": "Sub"}}],
                    "coach": {"id": 1, "name": "Coach"},
                },
                {
                    "team": {"id": 2, "name": "Away FC"},
                    "startXI": [{"player": {"id": 30, "name": "AwayStarter"}}],
                    "substitutes": [{"player": {"id": 40, "name": "AwaySub"}}],
                    "coach": {"id": 2, "name": "Away Coach"},
                },
            ],
        )
    ]
    team_service = FakeTeamService({
        1: {"team_id": 1, "players": [{"player_id": 10, "photo": "home.png"}]},
        2: {"team_id": 2, "players": [{"player_id": 30, "photo": "away.png"}]},
    })
    service = LineupService(client=client, cache_service=cache, team_service=team_service)

    result = asyncio.run(service.get_cached_match_lineup(db, 123))

    assert result[0]["startXI"][0]["player"]["photo"] == "home.png"
    assert result[0]["substitutes"][0]["player"]["photo"] is None
    assert result[1]["startXI"][0]["player"]["photo"] == "away.png"
    assert result[1]["coach"]["name"] == "Away Coach"
    assert team_service.calls == [1, 2]


def test_get_cached_match_lineup_cache_hit_returns_cached_without_db_or_api():
    client = FakeClient([])
    cache = FakeCacheService()
    cache_key = make_lineup_cache_key(123)
    cache.store[cache_key] = [{"team": {"id": 1}}]
    db = FakeSession()
    service = LineupService(client=client, cache_service=cache)

    result = asyncio.run(service.get_cached_match_lineup(db, 123))

    assert result == [{"team": {"id": 1}}]
    assert client.calls == 0
    assert cache.get_calls == [cache_key]
    assert cache.set_calls == []


def test_get_cached_match_lineup_cache_miss_uses_database_fallback_and_sets_cache_with_ttl():
    client = FakeClient([])
    cache = FakeCacheService()
    db = FakeSession()
    db.records[123] = [MatchLineup(match_id=123, data=[{"team": {"id": 1}}])]
    service = LineupService(client=client, cache_service=cache)

    result = asyncio.run(service.get_cached_match_lineup(db, 123))

    assert result == [{"team": {"id": 1}}]
    assert client.calls == 0
    assert cache.get_calls == [make_lineup_cache_key(123)]
    assert len(cache.set_calls) == 1
    assert cache.set_calls[0][0] == make_lineup_cache_key(123)
    assert cache.set_calls[0][2] == settings.REDIS_TTL_LINEUP


def test_get_cached_match_lineup_cache_miss_syncs_and_sets_cache_with_ttl():
    client = FakeClient([_valid_lineup(1, "Coach A")])
    cache = FakeCacheService()
    db = FakeSession(statuses={123: "NS"})
    sync_service = FakeLineupSyncService({"success": True, "match_id": 123, "created": True, "updated": False})
    service = LineupService(
        client=client,
        cache_service=cache,
        lineup_sync_service=sync_service,
    )

    result = asyncio.run(service.get_cached_match_lineup(db, 123))

    assert result is None
    assert client.calls == 0
    assert sync_service.calls == []
    assert cache.get_calls == [make_lineup_cache_key(123)]
    assert cache.set_calls == []


def test_get_cached_match_lineup_database_fallback_returns_none_when_no_data():
    client = FakeClient([{"response": []}])
    cache = FakeCacheService()
    db = FakeSession(statuses={123: "NS"})
    sync_service = FakeLineupSyncService({"success": True, "match_id": 123, "created": True, "updated": False})
    service = LineupService(
        client=client,
        cache_service=cache,
        lineup_sync_service=sync_service,
    )

    result = asyncio.run(service.get_cached_match_lineup(db, 123))

    assert result is None
    assert client.calls == 0
    assert sync_service.calls == []
    assert cache.get_calls == [make_lineup_cache_key(123)]
    assert cache.set_calls == []


def test_sync_lineup_created_log_uses_safe_extra_fields(caplog):
    client = FakeClient([_valid_lineup(1, "Coach A")])
    cache = FakeCacheService()
    db = FakeSession(statuses={123: "NS"})
    service = LineupService(client=client, cache_service=cache)

    with caplog.at_level("INFO"):
        result = asyncio.run(service.sync_lineup(db, 123))

    assert result["success"] is True
    created_record = next(record for record in caplog.records if record.msg == "LINEUP_SYNC_CREATED")
    assert getattr(created_record, "lineup_created") is True
    assert getattr(created_record, "lineup_updated") is False


def test_sync_lineup_updated_log_uses_safe_extra_fields(caplog):
    client = FakeClient([_valid_lineup(1, "Coach B")])
    cache = FakeCacheService()
    db = FakeSession(statuses={123: "NS"})
    db.records[123] = [MatchLineup(match_id=123, data=_valid_lineup(1, "Coach A")["response"])]
    service = LineupService(client=client, cache_service=cache)

    with caplog.at_level("INFO"):
        result = asyncio.run(service.sync_lineup(db, 123))

    assert result["success"] is True
    updated_record = next(record for record in caplog.records if record.msg == "LINEUP_SYNC_UPDATED")
    assert getattr(updated_record, "lineup_created") is False
    assert getattr(updated_record, "lineup_updated") is True
