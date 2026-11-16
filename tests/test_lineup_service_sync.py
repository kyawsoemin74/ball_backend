import asyncio
from copy import deepcopy
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from app.api import matches as matches_api
from app.core.config import settings
from app.core.security import get_current_user
from app.models.match_lineup import MatchLineup
from app.providers.lineup_provider import LineupProvider
from app.services.base.football_client import FootballAPIResponse
from app.services.lineup_service import LineupService as RealLineupService, make_lineup_cache_key
from app.services.lineup_sync_service import LineupSyncService
from app.services.player_identity_resolution_service import PlayerReadiness
from app.services.resource_lock import run_with_resource_lock


class FakeIdentityResolver:
    def __init__(self):
        self.calls = 0

    async def resolve_lineup(self, db, payload):
        self.calls += 1
        canonical = []
        results = []
        for lineup in payload:
            canonical_lineup = dict(lineup)
            for section in ("startXI", "substitutes"):
                entries = []
                for position, entry in enumerate(lineup.get(section, []), start=1):
                    player = dict(entry["player"])
                    player["player_id"] = int(player["id"]) + 1000
                    entries.append({**entry, "player": player})
                    results.append(PlayerReadiness("READY", player["player_id"], "api-football", str(player["id"]), lineup["team"]["id"], player.get("name")))
                canonical_lineup[section] = entries
            canonical.append(canonical_lineup)
        return results, canonical


def LineupService(*args, **kwargs):
    kwargs.setdefault("player_identity_resolution_service", FakeIdentityResolver())
    return RealLineupService(*args, **kwargs)


class FakeClient:
    def __init__(self, responses: List[Dict[str, Any]]):
        self._responses = list(responses)
        self.calls = 0
        self.requests: List[tuple[str, Dict[str, Any]]] = []

    async def get(self, path: str, params: Dict[str, Any]) -> Dict[str, Any]:
        self.calls += 1
        self.requests.append((path, params))
        return self._responses.pop(0)


class FakeMetadataClient:
    def __init__(self, response: FootballAPIResponse):
        self.response = response
        self.requests = []

    async def get_with_metadata(self, path, params=None):
        self.requests.append((path, params))
        return self.response


class FakeAnalyticsProjection:
    def __init__(self):
        self.calls = []

    async def project_lineup(self, db, match_id, payload):
        self.calls.append((db, match_id, payload))
        return {"success": True}


def test_lineup_provider_uses_metadata_response_without_changing_request():
        payload = _valid_lineup(1, "Coach")
        client = FakeMetadataClient(FootballAPIResponse(payload=payload, status_code=200))

        result = asyncio.run(LineupProvider(client).get_match_lineup(456))

        assert result == payload
        assert client.requests == [("/fixtures/lineups", {"fixture": 456})]


@pytest.mark.parametrize("errors", [{}, [], None, "", {"message": ""}, {"details": {}}])
def test_lineup_provider_accepts_empty_errors(errors):
        payload = _valid_lineup(1, "Coach")
        payload["errors"] = errors
        client = FakeMetadataClient(
            FootballAPIResponse(
                payload=payload,
                status_code=200,
                error_message="{}" if errors == {"details": {}} else None,
            )
        )

        result = asyncio.run(LineupProvider(client).get_match_lineup(456))

        assert result == payload


def test_lineup_provider_rejects_meaningful_errors_even_with_valid_response():
        payload = _valid_lineup(1, "Coach")
        payload["errors"] = {"token": "invalid"}
        client = FakeMetadataClient(FootballAPIResponse(payload=payload, status_code=200))

        result = asyncio.run(LineupProvider(client).get_match_lineup(456))

        assert result is None


def test_lineup_provider_rejects_http_failure_even_with_valid_payload():
        payload = _valid_lineup(1, "Coach")
        client = FakeMetadataClient(
            FootballAPIResponse(payload=payload, status_code=503, error_code="http_error")
        )

        result = asyncio.run(LineupProvider(client).get_match_lineup(456))

        assert result is None


@pytest.mark.parametrize(
        "payload",
        [
            None,
            {},
            {"response": None},
            {"response": {}},
            {"response": []},
        ],
)
def test_lineup_provider_rejects_unusable_response_envelopes(payload):
        client = FakeMetadataClient(FootballAPIResponse(payload=payload, status_code=200))

        result = asyncio.run(LineupProvider(client).get_match_lineup(456))

        assert result is None


def test_lineup_provider_fallback_rejects_provider_errors():
        payload = _valid_lineup(1, "Coach")
        payload["errors"] = ["provider error"]
        client = FakeClient([payload])

        result = asyncio.run(LineupProvider(client).get_match_lineup(456))

        assert result is None
        assert client.requests == [("/fixtures/lineups", {"fixture": 456})]


def test_lineup_response_validator_accepts_complete_team_shapes_and_missing_player_ids():
        service = LineupService(client=FakeClient([]), cache_service=FakeCacheService())
        payload = _valid_lineup(1, "Coach")["response"]
        payload[0]["startXI"][0]["player"].pop("id")

        assert service._is_valid_lineup_response(payload) is True


@pytest.mark.parametrize(
        "mutate",
        [
            lambda payload: payload[1]["team"].update(id=payload[0]["team"]["id"]),
            lambda payload: payload[0]["team"].update(id=0),
            lambda payload: payload[0].update(startXI="invalid"),
            lambda payload: payload[0].update(substitutes=None),
            lambda payload: payload[0].update(formation="4--3-3"),
            lambda payload: payload[0]["startXI"][0].update(player=[]),
            lambda payload: payload[1]["startXI"][0]["player"].update(id=10),
            lambda payload: payload[0]["startXI"][0]["player"].update(id=True),
        ],
)
def test_lineup_response_validator_rejects_invalid_team_or_player_shapes(mutate):
        service = LineupService(client=FakeClient([]), cache_service=FakeCacheService())
        payload = deepcopy(_valid_lineup(1, "Coach")["response"])
        mutate(payload)

        assert service._is_valid_lineup_response(payload) is False


def test_lineup_response_validator_rejects_malformed_top_level_response():
        service = LineupService(client=FakeClient([]), cache_service=FakeCacheService())

        assert service._is_valid_lineup_response(None) is False
        assert service._is_valid_lineup_response([]) is False


class FakeScalarResult:
    def __init__(self, rows: List[MatchLineup]):
        self._rows = rows

    def scalar_one_or_none(self):
        if not self._rows:
            return None
        if len(self._rows) > 1:
            raise RuntimeError("Multiple rows found")
        return self._rows[0]

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)


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


class FailingCacheService(FakeCacheService):
    async def get_json(self, key: str) -> Any:
        raise RuntimeError("redis unavailable")

    async def set_json(self, key: str, value: Any, ttl: int) -> None:
        raise RuntimeError("redis unavailable")


class FakeReadPlayerService:
    def __init__(self, players=None, *, batch_error=False, failing_provider_ids=None):
        self.players = players or {}
        self.batch_error = batch_error
        self.failing_provider_ids = set(failing_provider_ids or ())
        self.batch_calls = []
        self.provider_calls = []
        self.local_calls = []
        self.local_batch_calls = []

    async def get_players_by_provider_ids(self, db, provider_ids):
        self.batch_calls.append(list(provider_ids))
        if self.batch_error:
            raise RuntimeError("batch lookup failed")
        return [self.players[value] for value in provider_ids if value in self.players]

    async def get_player_by_provider_id(self, db, provider_id, provider="api-football"):
        self.provider_calls.append(provider_id)
        if provider_id in self.failing_provider_ids:
            raise RuntimeError("player lookup failed")
        return self.players.get(provider_id)

    async def get_player(self, db, player_id):
        self.local_calls.append(player_id)
        return self.players.get(str(player_id))

    async def get_players_by_ids(self, db, player_ids):
        self.local_batch_calls.append(list(player_ids))
        return [
            player
            for player in self.players.values()
            if player.player_id in player_ids
        ]


class FakeTeamService:
    def __init__(self, squad_payloads: Dict[int, Dict[str, Any]]):
        self.squad_payloads = squad_payloads
        self.calls: List[int] = []
        self.team_repository = self

    async def find_by_provider_identity(self, db, provider, provider_id):
        return SimpleNamespace(team_id=provider_id)

    async def get_cached_team_squad(self, db, team_id: int) -> Dict[str, Any] | None:
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
    def __init__(
        self,
        statuses: Dict[int, str] | None = None,
        provider_fixture_ids: Dict[int, Any] | None = None,
        providers: Dict[int, str | None] | None = None,
        missing_match_ids: set[int] | None = None,
        team_mappings: Dict[str, int] | None = None,
        missing_team_provider_ids: set[str] | None = None,
    ):
        self.records: Dict[int, List[MatchLineup]] = {}
        self._pending: List[MatchLineup] = []
        self.statuses = statuses or {}
        self.provider_fixture_ids = provider_fixture_ids or {}
        self.providers = providers or {}
        self.missing_match_ids = missing_match_ids or set()
        self.team_mappings = team_mappings or {}
        self.missing_team_provider_ids = missing_team_provider_ids or set()

    async def execute(self, statement, params=None):
        model_name = self._extract_model_name(statement)
        match_id = self._extract_match_id(statement)

        if model_name == "Match":
            if match_id in self.missing_match_ids:
                return FakeScalarResult([])
            status = self.statuses.get(match_id, "NS")
            row = SimpleNamespace(
                match_id=match_id,
                status=status,
                provider=self.providers.get(match_id, "api-football"),
                provider_fixture_id=self.provider_fixture_ids.get(match_id, match_id),
                home_team_id=1,
                away_team_id=2,
            )
            return FakeScalarResult([row])

        if model_name == "Team":
            provider_id = self._extract_filter_value(statement, "provider_id")
            provider_id_key = str(provider_id)
            if provider_id_key in self.missing_team_provider_ids:
                return FakeScalarResult([])
            local_team_id = self.team_mappings.get(provider_id_key)
            if local_team_id is None:
                try:
                    local_team_id = int(provider_id)
                except (TypeError, ValueError):
                    return FakeScalarResult([])
            return FakeScalarResult([SimpleNamespace(team_id=local_team_id)])

        if model_name == "MissingLineupIdentity":
            return FakeScalarResult([])

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
    def _extract_filter_value(statement, column_name: str):
        for criterion in getattr(statement, "_where_criteria", []):
            left = getattr(criterion, "left", None)
            if getattr(left, "name", None) == column_name:
                right = getattr(criterion, "right", None)
                return getattr(right, "value", None)
        return None

    @staticmethod
    def _extract_model_name(statement) -> str | None:
        descriptions = getattr(statement, "column_descriptions", [])
        if not descriptions:
            return None
        entity = descriptions[0].get("entity")
        if entity is None:
            return None
        return getattr(entity, "__name__", None)


class ReadOnlyFakeSession(FakeSession):
    def __init__(self):
        super().__init__()
        self.write_calls = []

    def add(self, row):
        self.write_calls.append(("add", row))

    async def flush(self):
        self.write_calls.append(("flush",))

    async def commit(self):
        self.write_calls.append(("commit",))

    async def rollback(self):
        self.write_calls.append(("rollback",))


def _valid_lineup(team_id: int, coach_name: str) -> Dict[str, Any]:
    away_team_id = 2 if team_id != 2 else 1

    def team_lineup(current_team_id: int, current_coach: str, player_ids: tuple[int, int], name: str):
        return {
            "team": {"id": current_team_id, "name": name},
            "formation": "4-3-3",
            "startXI": [{"player": {"id": player_ids[0], "name": "Starter"}}],
            "substitutes": [{"player": {"id": player_ids[1], "name": "Sub"}}],
            "coach": {"id": current_team_id, "name": current_coach},
        }

    return {
        "response": [
            team_lineup(team_id, coach_name, (10, 20), "Example FC"),
            team_lineup(away_team_id, f"{coach_name} Away", (11, 21), "Example United"),
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


def test_sync_lineup_does_not_invalidate_cache_from_service_layer():
    db = FakeSession()
    cache = FakeCacheService()
    service = LineupService(client=FakeClient([_valid_lineup(1, "Coach A")]), cache_service=cache)

    result = asyncio.run(service.sync_lineup(db, 123, invalidate_cache=True))

    assert result["success"] is True
    assert cache.delete_calls == []


def test_sync_lineup_rejects_non_participant_team_before_write():
    db = FakeSession()
    class TeamRepository:
        async def find_by_provider_identity(self, db, provider, provider_id):
            return SimpleNamespace(team_id=int(provider_id))

    sync_service = LineupSyncService(
        lineup_provider=LineupProvider(FakeClient([_valid_lineup(99, "Coach")])),
        team_repository=TeamRepository(),
    )
    service = LineupService(
        client=FakeClient([]),
        cache_service=FakeCacheService(),
        lineup_sync_service=sync_service,
    )

    result = asyncio.run(service.sync_lineup(db, 123))

    assert result["success"] is False
    assert result["failure_classification"] == "TEAM_NOT_MATCH_PARTICIPANT"
    assert 123 not in db.records


@pytest.mark.parametrize("has_existing_lineup", [False, True], ids=["insert", "update"])
def test_integrity_error_stops_before_analytics_and_owner_rolls_back_under_lock(has_existing_lineup):
    class LockResult:
        def scalar_one(self):
            return True

    class IntegrityFailSession(FakeSession):
        def __init__(self):
            super().__init__()
            self.events = []
            self.lock_held = False
            self.failed_flush = False
            self.rollback_calls = 0
            self.committed_records = deepcopy(self.records)

        async def execute(self, statement, params=None):
            if "pg_try_advisory_xact_lock" in str(statement):
                self.lock_held = True
                self.events.append("lock_acquired")
                return LockResult()
            if self.failed_flush:
                self.events.append("query_after_failed_flush")
            return await super().execute(statement)

        async def flush(self):
            self.events.append("lineup_flush_failed")
            self.failed_flush = True
            raise IntegrityError("duplicate lineup", None, None)

        async def rollback(self):
            assert self.lock_held
            self.events.append("owner_rollback")
            self.rollback_calls += 1
            self.lock_held = False
            self.records = deepcopy(self.committed_records)
            await super().rollback()

    db = IntegrityFailSession()
    if has_existing_lineup:
        db.records[123] = [MatchLineup(match_id=123, data=[{"team": {"id": "old"}}])]
        db.committed_records = deepcopy(db.records)
    analytics = FakeAnalyticsProjection()
    sync = LineupSyncService(
        lineup_provider=LineupProvider(FakeClient([_valid_lineup(1, "Coach")])),
        analytics_projection_service=analytics,
        player_identity_resolution_service=FakeIdentityResolver(),
    )

    async def protected_sync():
        result = await sync.sync_lineup(db, 123, validate_lineup=lambda payload: True)
        assert db.lock_held
        if not result["success"]:
            await db.rollback()
        return result

    locked, result = asyncio.run(
        run_with_resource_lock(db, "lineup", 123, protected_sync)
    )

    assert locked is True
    assert result["success"] is False
    assert analytics.calls == []
    assert db.rollback_calls == 1
    assert db.events == ["lock_acquired", "lineup_flush_failed", "owner_rollback"]
    if has_existing_lineup:
        assert db.records[123][0].data == [{"team": {"id": "old"}}]
    else:
        assert db.records == {}
    assert db._pending == []


def test_lineup_sync_payload_validator_rejects_duplicate_canonical_players():
    service = LineupSyncService()
    payload = [
        {
            "team": {"id": 1},
            "formation": "4-3-3",
            "startXI": [
                {"player": {"local_player_id": 101, "provider_player_id": "1"}},
            ],
            "substitutes": [
                {"player": {"local_player_id": 101, "provider_player_id": "1"}},
            ],
        }
    ]

    result = service._validate_canonical_lineup_payload(payload)

    assert result == "INVALID_LINEUP_PAYLOAD"


def test_lineup_sync_payload_validator_rejects_starting_substitute_conflict():
    service = LineupSyncService()
    payload = [
        {
            "team": {"id": 1},
            "formation": "4-3-3",
            "startXI": [
                {"player": {"local_player_id": 101, "provider_player_id": "1"}},
            ],
            "substitutes": [
                {"player": {"local_player_id": 101, "provider_player_id": "1"}},
            ],
        }
    ]

    result = service._validate_canonical_lineup_payload(payload)

    assert result == "INVALID_LINEUP_PAYLOAD"


def test_get_cached_match_lineup_falls_back_when_redis_is_unavailable():
    db = FakeSession()
    db.records[123] = [MatchLineup(match_id=123, data=[{"team": {"id": 1}}])]
    service = LineupService(client=FakeClient([]), cache_service=FailingCacheService())

    result = asyncio.run(service.get_cached_match_lineup(db, 123))

    assert result[0]["local_match_id"] == 123


def _photo_lineup():
    return [{
        "team": {"id": 11},
        "startXI": [
            {"player": {"id": "p1", "player_id": 101, "name": "Photo Starter"}},
            {"player": {"id": "p2", "player_id": 102, "name": "No Photo"}},
            {"player": {"id": "missing", "player_id": 103, "name": "Unresolved"}},
            {"player": {"id": "conflict", "player_id": 999, "name": "Conflicting IDs"}},
        ],
        "substitutes": [
            {"player": {"player_id": 104, "name": "Local ID Substitute"}},
            {"player": {"id": "fails", "player_id": 105, "name": "Lookup Failure"}},
        ],
    }]


@pytest.mark.parametrize("cache_hit", [True, False], ids=["cache-hit", "cache-miss"])
def test_lineup_read_enriches_photos_without_mutating_raw_data(cache_hit):
    db = ReadOnlyFakeSession()
    cache = FakeCacheService()
    raw = _photo_lineup()
    if cache_hit:
        cache.store[make_lineup_cache_key(123)] = raw
    else:
        db.records[123] = [MatchLineup(match_id=123, data=raw)]

    players = FakeReadPlayerService(
        {
            "p1": SimpleNamespace(player_id=101, provider="api-football", provider_id="p1", photo="https://img/p1.png"),
            "p2": SimpleNamespace(player_id=102, provider="api-football", provider_id="p2", photo=None),
            "conflict": SimpleNamespace(player_id=109, provider="api-football", provider_id="conflict", photo="https://img/conflict.png"),
            "104": SimpleNamespace(player_id=104, provider="api-football", provider_id="p4", photo="https://img/p4.png"),
            "fails": SimpleNamespace(player_id=105, provider="api-football", provider_id="fails", photo="https://img/fails.png"),
        },
        batch_error=True,
        failing_provider_ids={"fails"},
    )
    service = LineupService(client=FakeClient([]), cache_service=cache, player_service=players)

    result = asyncio.run(service.get_cached_match_lineup(db, 123))

    starters = result[0]["startXI"]
    substitutes = result[0]["substitutes"]
    assert starters[0]["player"]["photo"] == "https://img/p1.png"
    assert "photo" not in starters[1]["player"]
    assert "photo" not in starters[2]["player"]
    assert "photo" not in starters[3]["player"]
    assert "photo" not in substitutes[0]["player"]
    assert substitutes[1]["player"]["photo"] == "https://img/fails.png"
    assert len(starters) == 4
    assert len(substitutes) == 2
    assert players.batch_calls == [["p1", "p2", "missing", "conflict", "fails"]]
    assert players.provider_calls == []
    assert players.local_calls == []
    assert players.local_batch_calls == [[101, 102, 103, 999, 105]]
    assert db.write_calls == []

    if cache_hit:
        assert cache.set_calls == []
        assert "photo" not in raw[0]["startXI"][0]["player"]
    else:
        assert len(cache.set_calls) == 1
        cached_data = cache.set_calls[0][1]["data"]
        assert "photo" not in cached_data[0]["startXI"][0]["player"]
        assert "photo" not in raw[0]["startXI"][0]["player"]


def test_lineup_read_rejects_provider_and_local_identity_conflict():
    db = ReadOnlyFakeSession()
    raw = [{
        "team": {"id": 11},
        "startXI": [{"player": {"id": "p1", "player_id": 999, "name": "Conflict"}}],
        "substitutes": [],
    }]
    cache = FakeCacheService()
    cache.store[make_lineup_cache_key(123)] = raw
    players = FakeReadPlayerService({"p1": SimpleNamespace(player_id=101, provider_id="p1", photo="https://img/p1.png")})
    service = LineupService(client=FakeClient([]), cache_service=cache, player_service=players)

    result = asyncio.run(service.get_cached_match_lineup(db, 123))

    assert "photo" not in result[0]["startXI"][0]["player"]
    assert result[0]["startXI"][0]["player"]["local_player_id"] == 999
    assert len(result[0]["startXI"]) == 1


def test_get_match_lineup_uses_cached_read_path():
    cache = FakeCacheService()
    cache_key = make_lineup_cache_key(123)
    cache.store[cache_key] = [{"team": {"id": 1}, "startXI": [], "substitutes": []}]
    service = LineupService(client=FakeClient([]), cache_service=cache)

    result = asyncio.run(service.get_match_lineup(123))

    assert result == [{"team": {"id": 1}, "startXI": [], "substitutes": [], "local_match_id": 123}]


def test_sync_lineup_creates_new_row():
    db = FakeSession()
    cache = FakeCacheService()
    service = LineupService(client=FakeClient([_valid_lineup(1, "Coach A")]), cache_service=cache)

    result = asyncio.run(service.sync_lineup(db, 123))

    assert result == {"success": True, "match_id": 123, "created": True, "updated": False}
    assert 123 in db.records
    assert len(db.records[123]) == 1
    assert [lineup["local_team_id"] for lineup in db.records[123][0].data] == [1, 2]
    assert cache.delete_calls == []


@pytest.mark.parametrize(
    ("case", "team_mappings", "missing_team_provider_ids"),
    [
        ("home_only", {}, set()),
        ("away_only", {}, set()),
        ("duplicate_provider_team", {}, set()),
        ("unknown_team", {}, {"2"}),
        ("non_participant", {"2": 99}, set()),
        ("duplicate_local_team", {"2": 1}, set()),
    ],
)
def test_invalid_team_sets_preserve_existing_lineup_and_stop_before_resolution(
    case,
    team_mappings,
    missing_team_provider_ids,
):
    source_payload = _valid_lineup(1, "Current Coach")
    response = deepcopy(source_payload["response"])
    if case == "home_only":
        response = response[:1]
    elif case == "away_only":
        response = response[1:]
    elif case == "duplicate_provider_team":
        response[1]["team"]["id"] = response[0]["team"]["id"]

    db = FakeSession(
        team_mappings=team_mappings,
        missing_team_provider_ids=missing_team_provider_ids,
    )
    existing = MatchLineup(match_id=123, data=deepcopy(source_payload["response"]))
    db.records[123] = [existing]
    existing_data = deepcopy(existing.data)
    resolver = FakeIdentityResolver()
    analytics = FakeAnalyticsProjection()
    client = FakeClient([{"response": response}])
    sync_service = LineupSyncService(
        lineup_provider=LineupProvider(client),
        analytics_projection_service=analytics,
        player_identity_resolution_service=resolver,
    )
    service = RealLineupService(
        client=client,
        cache_service=FakeCacheService(),
        lineup_sync_service=sync_service,
    )

    result = asyncio.run(service.sync_lineup(db, 123))

    assert result["success"] is False
    assert resolver.calls == 0
    assert analytics.calls == []
    assert len(db.records[123]) == 1
    assert existing.data == existing_data


def test_provider_errors_fail_before_team_or_player_resolution():
    valid_payload = _valid_lineup(1, "Stored Coach")
    payload = _valid_lineup(1, "Provider Coach")
    payload["errors"] = {"token": "invalid"}
    db = FakeSession()
    existing = MatchLineup(match_id=123, data=deepcopy(valid_payload["response"]))
    db.records[123] = [existing]
    existing_data = deepcopy(existing.data)
    resolver = FakeIdentityResolver()
    analytics = FakeAnalyticsProjection()
    client = FakeClient([payload])
    sync_service = LineupSyncService(
        lineup_provider=LineupProvider(client),
        analytics_projection_service=analytics,
        player_identity_resolution_service=resolver,
    )
    service = RealLineupService(
        client=client,
        cache_service=FakeCacheService(),
        lineup_sync_service=sync_service,
    )

    result = asyncio.run(service.sync_lineup(db, 123))

    assert result["success"] is False
    assert resolver.calls == 0
    assert analytics.calls == []
    assert existing.data == existing_data


def test_complete_team_set_reaches_identity_and_analytics():
    class WritingAnalytics(FakeAnalyticsProjection):
        async def project_lineup(self, db, match_id, payload):
            db.analytics_rows = deepcopy(payload)
            return await super().project_lineup(db, match_id, payload)

    db = FakeSession()
    db.analytics_rows = []
    resolver = FakeIdentityResolver()
    analytics = WritingAnalytics()
    client = FakeClient([_valid_lineup(1, "Coach")])
    sync_service = LineupSyncService(
        lineup_provider=LineupProvider(client),
        analytics_projection_service=analytics,
        player_identity_resolution_service=resolver,
    )
    service = RealLineupService(
        client=client,
        cache_service=FakeCacheService(),
        lineup_sync_service=sync_service,
    )

    result = asyncio.run(service.sync_lineup(db, 123))

    assert result["success"] is True
    assert resolver.calls == 1
    assert len(analytics.calls) == 1
    analytics_db, analytics_match_id, analytics_payload = analytics.calls[0]
    assert analytics_db is db
    assert analytics_match_id == 123
    assert analytics_payload == db.records[123][0].data
    assert [lineup["local_team_id"] for lineup in analytics_payload] == [1, 2]
    asyncio.run(db.commit())
    assert db.analytics_rows == db.records[123][0].data


def test_analytics_failure_leaves_all_writes_for_outer_rollback():
    class TransactionalSession(FakeSession):
        def __init__(self):
            super().__init__()
            self.analytics_rows = [{"old": "analytics"}]
            self.player_rows = []
            self.rollback_calls = 0

        async def rollback(self):
            self.rollback_calls += 1
            self.records = deepcopy(self.committed_records)
            self._pending.clear()
            self.analytics_rows = deepcopy(self.committed_analytics_rows)
            self.player_rows = list(self.committed_player_rows)

    class TransactionAwareResolver(FakeIdentityResolver):
        async def resolve_lineup(self, db, payload):
            db.player_rows.append("player-master-upsert")
            return await super().resolve_lineup(db, payload)

    class FailingAnalytics:
        def __init__(self):
            self.calls = []

        async def project_lineup(self, db, match_id, payload):
            self.calls.append((db, match_id, payload))
            db.analytics_rows = deepcopy(payload)
            raise RuntimeError("analytics projection failed")

    db = TransactionalSession()
    existing = MatchLineup(match_id=123, data=[{"team": {"id": "old"}}])
    db.records[123] = [existing]
    db.committed_records = deepcopy(db.records)
    db.committed_analytics_rows = deepcopy(db.analytics_rows)
    db.committed_player_rows = list(db.player_rows)
    old_lineup = deepcopy(existing.data)
    old_analytics = deepcopy(db.analytics_rows)
    analytics = FailingAnalytics()
    sync = LineupSyncService(
        lineup_provider=LineupProvider(FakeClient([_valid_lineup(1, "Coach")])),
        analytics_projection_service=analytics,
        player_identity_resolution_service=TransactionAwareResolver(),
    )

    result = asyncio.run(sync.sync_lineup(db, 123, validate_lineup=lambda payload: True))

    assert result["success"] is False
    assert len(analytics.calls) == 1
    assert analytics.calls[0][0] is db
    assert db.rollback_calls == 0
    assert db.records[123][0].data != old_lineup
    assert db.analytics_rows != old_analytics
    assert db.player_rows == ["player-master-upsert"]

    asyncio.run(db.rollback())

    assert db.rollback_calls == 1
    assert db.records[123][0].data == old_lineup
    assert db.analytics_rows == old_analytics
    assert db.player_rows == []


@pytest.mark.parametrize("local_match_id,provider_fixture_id", [(123, 456), (123, 123)])
def test_sync_lineup_requests_provider_fixture_identity(local_match_id, provider_fixture_id):
    db = FakeSession(provider_fixture_ids={local_match_id: provider_fixture_id})
    client = FakeClient([_valid_lineup(1, "Coach")])
    service = LineupService(client=client, cache_service=FakeCacheService())

    result = asyncio.run(service.sync_lineup(db, local_match_id))

    assert result["success"] is True
    assert client.requests == [("/fixtures/lineups", {"fixture": provider_fixture_id})]
    if local_match_id != provider_fixture_id:
        assert client.requests[0][1]["fixture"] != local_match_id


@pytest.mark.parametrize(
    ("session_kwargs", "expected_reason"),
    [
        ({"missing_match_ids": {123}}, "match_not_found"),
        ({"provider_fixture_ids": {123: None}}, "provider_fixture_id_missing"),
        ({"providers": {123: "other-provider"}}, "unsupported_provider"),
        ({"provider_fixture_ids": {123: 0}}, "invalid_provider_fixture_id"),
        ({"provider_fixture_ids": {123: -456}}, "invalid_provider_fixture_id"),
        ({"provider_fixture_ids": {123: "456"}}, "invalid_provider_fixture_id"),
    ],
)
def test_sync_lineup_rejects_invalid_match_provider_mapping(session_kwargs, expected_reason):
    db = FakeSession(**session_kwargs)
    client = FakeClient([])
    service = LineupService(client=client, cache_service=FakeCacheService())

    result = asyncio.run(service.sync_lineup(db, 123))

    assert result["success"] is False
    assert result["reason"] == expected_reason
    assert client.calls == 0
    assert 123 not in db.records


def test_manual_lineup_sync_route_keeps_local_match_id(monkeypatch):
    from app.api import matches as matches_api

    calls = []

    class ManualDB:
        async def commit(self):
            calls.append(("commit",))

        async def rollback(self):
            calls.append(("rollback",))

    class ManualCache:
        async def delete(self, key):
            calls.append(("cache", key))

    async def fake_sync_match_lineup(db, match_id):
        calls.append(("sync", match_id))
        return {"success": True, "match_id": match_id}

    async def fake_lock(db, resource_type, resource_identity, operation):
        calls.append(("lock", resource_type, resource_identity))
        return True, await operation()

    monkeypatch.setattr(matches_api.football_service, "sync_match_lineup", fake_sync_match_lineup)
    monkeypatch.setattr(matches_api, "run_with_resource_lock", fake_lock)
    monkeypatch.setattr(matches_api, "CacheService", ManualCache)

    result = asyncio.run(matches_api.sync_match_lineup_route(match_id=123, db=ManualDB()))

    assert result == {"success": True, "match_id": 123}
    assert ("lock", "lineup", 123) in calls
    assert ("sync", 123) in calls


def test_sync_lineup_updates_existing_row():
    db = FakeSession()
    existing = MatchLineup(match_id=123, data=_valid_lineup(1, "Old Coach")["response"])
    db.records[123] = [existing]

    cache = FakeCacheService()
    client = FakeClient([_valid_lineup(1, "New Coach")])
    analytics = FakeAnalyticsProjection()
    sync_service = LineupSyncService(
        lineup_provider=LineupProvider(client),
        analytics_projection_service=analytics,
        player_identity_resolution_service=FakeIdentityResolver(),
    )
    service = LineupService(
        client=client,
        cache_service=cache,
        lineup_sync_service=sync_service,
    )
    result = asyncio.run(service.sync_lineup(db, 123))

    assert result == {
        "success": True,
        "match_id": 123,
        "created": False,
        "updated": True,
        "analytics": {"success": True},
    }
    assert len(db.records[123]) == 1
    assert db.records[123][0].data[0]["coach"]["name"] == "New Coach"
    assert len(analytics.calls) == 1
    assert analytics.calls[0][2] == db.records[123][0].data
    assert cache.delete_calls == []


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
    assert cache.delete_calls == []


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


def test_sync_lineup_terminal_skip_precedes_provider_identity_validation():
    client = FakeClient([])
    db = FakeSession(statuses={123: "FT"}, provider_fixture_ids={123: None})
    service = LineupService(client=client, cache_service=FakeCacheService())

    result = asyncio.run(service.sync_lineup(db, 123))

    assert result["success"] is True
    assert result["skipped"] is True
    assert result["reason"] == "status_blocked"
    assert client.calls == 0
    assert 123 not in db.records


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


def test_get_cached_match_lineup_does_not_fetch_squad_photos():
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

    assert result[0]["startXI"][0]["player"].get("photo") is None
    assert result[0]["substitutes"][0]["player"].get("photo") is None
    assert result[1]["startXI"][0]["player"].get("photo") is None
    assert result[1]["coach"]["name"] == "Away Coach"
    assert team_service.calls == []


def test_get_cached_match_lineup_cache_hit_returns_cached_without_db_or_api():
    client = FakeClient([])
    cache = FakeCacheService()
    cache_key = make_lineup_cache_key(123)
    cache.store[cache_key] = [{"team": {"id": 1}}]
    db = FakeSession()
    service = LineupService(client=client, cache_service=cache)

    result = asyncio.run(service.get_cached_match_lineup(db, 123))

    assert result == [{"team": {"id": 1}, "local_match_id": 123, "startXI": [], "substitutes": []}]
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

    assert result == [{"team": {"id": 1}, "local_match_id": 123, "startXI": [], "substitutes": []}]
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


def test_get_cached_match_lineup_does_not_reach_team_provider_enrichment_path():
    class TeamServiceSpy:
        def __init__(self):
            self.calls = []

        async def get_cached_team_squad(self, db, team_id):
            self.calls.append(team_id)
            raise AssertionError("GET LINEUP must not reach TeamProvider-backed squad enrichment")

    cache = FakeCacheService()
    db = FakeSession()
    db.records[123] = [MatchLineup(match_id=123, data=[{"team": {"id": 1}}])]
    team_service = TeamServiceSpy()
    service = LineupService(client=FakeClient([]), cache_service=cache, team_service=team_service)

    result = asyncio.run(service.get_cached_match_lineup(db, 123))

    assert result == [{"team": {"id": 1}, "local_match_id": 123, "startXI": [], "substitutes": []}]
    assert team_service.calls == []


def test_canonical_response_uses_local_player_team_and_match_identity_shape():
    payload = [
        {
            "local_team_id": 7,
            "team": {"id": 99, "name": "Home FC"},
            "local_match_id": 123,
            "startXI": [
                {"player": {"id": 500, "player_id": 11, "name": "Player One"}},
            ],
            "substitutes": [
                {"player": {"id": 501, "player_id": 12, "name": "Player Two"}},
            ],
        }
    ]

    response = RealLineupService._canonical_response(123, payload)

    assert response[0]["local_match_id"] == 123
    assert response[0]["team"] == {
        "local_team_id": 7,
        "provider_team_id": "99",
        "name": "Home FC",
    }
    assert response[0]["startXI"][0]["player"] == {
        "local_player_id": 11,
        "provider_player_id": "500",
        "name": "Player One",
    }
    assert response[0]["substitutes"][0]["player"] == {
        "local_player_id": 12,
        "provider_player_id": "501",
        "name": "Player Two",
    }


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


class LineupHTTPDB:
    def __init__(self):
        self.events = []

    async def commit(self):
        self.events.append("commit")

    async def rollback(self):
        self.events.append("rollback")


def _configured_lineup_http_app(monkeypatch, db, service_result, *, lock_acquired=True):
    test_app = FastAPI()
    test_app.include_router(matches_api.router, prefix="/api")
    calls = []

    async def override_db():
        yield db

    async def override_admin():
        return SimpleNamespace(role="admin", is_active=True)

    async def fake_sync(db, match_id):
        calls.append(match_id)
        if isinstance(service_result, Exception):
            raise service_result
        return service_result

    async def fake_lock(_db, resource_type, resource_identity, operation):
        assert (resource_type, resource_identity) == ("lineup", 123)
        if not lock_acquired:
            return False, None
        return True, await operation()

    class HttpCache:
        async def delete(self, _key):
            assert db.events[-1] == "commit"
            db.events.append("cache")
            return True

    test_app.dependency_overrides[matches_api.get_db] = override_db
    test_app.dependency_overrides[matches_api.current_active_admin] = override_admin
    monkeypatch.setattr(
        matches_api,
        "football_service",
        SimpleNamespace(sync_match_lineup=fake_sync),
    )
    monkeypatch.setattr(matches_api, "run_with_resource_lock", fake_lock)
    monkeypatch.setattr(matches_api, "CacheService", HttpCache)
    return test_app, calls


@pytest.mark.parametrize(
    "service_result",
    [
        {"success": True, "match_id": 123, "created": True, "updated": False},
        {"success": True, "partial": True, "reason": "missing_player_identity"},
        {"success": True, "skipped": True, "status": "FT"},
        {"success": False, "reason": "lineup_not_available"},
        {"success": False, "reason": "match_not_found"},
        {"success": False, "reason": "lineup_sync_failed"},
    ],
    ids=["complete", "partial", "skipped", "provider-invalid", "match-missing", "persistence-failed"],
)
def test_lineup_http_response_contract_preserves_service_result(
    monkeypatch, service_result
):
    db = LineupHTTPDB()
    test_app, calls = _configured_lineup_http_app(monkeypatch, db, service_result)

    response = TestClient(test_app).post("/api/matches/sync/123/lineup")

    assert response.status_code == 200
    assert response.json() == service_result
    assert calls == [123]
    if service_result["success"]:
        assert db.events == ["commit", "cache"]
    else:
        assert db.events == ["rollback"]


def test_lineup_http_lock_conflict_is_409_without_running_sync(monkeypatch):
    db = LineupHTTPDB()
    test_app, calls = _configured_lineup_http_app(
        monkeypatch,
        db,
        {"success": True},
        lock_acquired=False,
    )

    response = TestClient(test_app).post("/api/matches/sync/123/lineup")

    assert response.status_code == 409
    assert response.json() == {"detail": "Lineup sync already in progress"}
    assert calls == []
    assert db.events == []


def test_lineup_http_path_validation_and_admin_authentication():
    test_app = FastAPI()
    test_app.include_router(matches_api.router, prefix="/api")
    client = TestClient(test_app)

    assert client.post("/api/matches/sync/123/lineup").status_code == 401

    async def normal_user():
        return SimpleNamespace(role="user", is_active=True)

    test_app.dependency_overrides[get_current_user] = normal_user
    assert client.post("/api/matches/sync/123/lineup").status_code == 403

    async def admin_user():
        return SimpleNamespace(role="admin", is_active=True)

    test_app.dependency_overrides[matches_api.current_active_admin] = admin_user
    response = client.post("/api/matches/sync/0/lineup")

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["path", "match_id"]


def test_lineup_http_unexpected_exception_is_500_and_rolls_back(monkeypatch):
    db = LineupHTTPDB()
    test_app, calls = _configured_lineup_http_app(
        monkeypatch,
        db,
        RuntimeError("internal detail must not be returned"),
    )

    response = TestClient(test_app, raise_server_exceptions=False).post(
        "/api/matches/sync/123/lineup"
    )

    assert response.status_code == 500
    assert "internal detail must not be returned" not in response.text
    assert calls == [123]
    assert db.events == ["rollback"]
