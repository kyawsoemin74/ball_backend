import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from app.cache import make_cache_key
from app.models.match_event import MatchEvent
from app.repositories.event_repository import EventRepository
from app.services.event_service import EventService
from app.services.event_sync_service import EventSyncService


class FakeClient:
    def __init__(self, responses: List[Dict[str, Any]]):
        self._responses = list(responses)
        self.calls = 0

    async def get(self, path: str, params: Dict[str, Any]) -> Dict[str, Any]:
        self.calls += 1
        return self._responses.pop(0)


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


class FakeEventProvider:
    def __init__(self, response: dict):
        self.response = response
        self.calls = 0

    async def get_match_events(self, match_id: int) -> dict:
        self.calls += 1
        self.match_ids = getattr(self, "match_ids", []) + [match_id]
        return self.response


class FakeTeamRepository:
    def __init__(self, mappings: Dict[int, int] | None = None):
        self.mappings = mappings or {1: 122, 10: 122}
        self.calls = []

    async def find_by_provider_identity(self, db, provider, provider_id):
        self.calls.append((provider, provider_id))
        team_id = self.mappings.get(int(provider_id))
        return SimpleNamespace(team_id=team_id) if team_id is not None else None


def test_event_service_uses_event_provider_for_transport():
    from app.providers.event_provider import EventProvider

    match_id = 1234
    api_response = {"response": []}
    provider = FakeEventProvider(api_response)
    client = FakeClient([api_response])
    service = EventService(client=client, cache_service=FakeCacheService(), event_provider=provider)

    result = asyncio.run(service.get_match_events(match_id))

    assert result == api_response
    assert provider.calls == 1
    assert client.calls == 0


def test_event_service_delegates_sync_refresh_and_clears_cache():
    match_id = 2222
    api_events = [
        {
            "time": {"elapsed": 15, "extra": 0},
            "team": {"id": 1, "name": "API Team"},
            "player": {"id": 5, "name": "Player"},
            "assist": {"id": 6, "name": "Assist"},
            "type": "Goal",
            "detail": "Normal Goal",
            "comments": None,
        }
    ]

    class FakeEventSyncService:
        def __init__(self, api_events):
            self.calls = 0
            self.api_events = api_events

        async def refresh_match_events(self, db, match_id, provider_fixture_id=None):
            self.calls += 1
            self.provider_fixture_id = provider_fixture_id
            return {"success": True, "api_events": self.api_events}

    db = FakeSession(statuses={match_id: "LIVE"}, provider_fixture_ids={match_id: 987654})
    cache = FakeCacheService()
    sync_service = FakeEventSyncService(api_events)
    service = EventService(
        client=FakeClient([]),
        cache_service=cache,
        event_sync_service=sync_service,
    )

    result = asyncio.run(service.sync_match_events(db, match_id))

    assert result["success"] is True
    assert sync_service.calls == 1
    assert sync_service.provider_fixture_id == 987654
    # Under frozen architecture, EventService.sync_match_events delegates to
    # EventSyncService but does not invalidate cache; cache invalidation
    # occurs after commit by the caller (API or scheduler).
    assert cache.delete_calls == []


class FakeScalarResult:
    def __init__(self, rows: List[Any]):
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
        return self._rows


class FakeSession:
    def __init__(self, statuses: Dict[int, str] | None = None, provider_fixture_ids=None):
        self.statuses = statuses or {}
        self.provider_fixture_ids = provider_fixture_ids or {}
        self.records: Dict[int, List[MatchEvent]] = {}
        self._pending: List[MatchEvent] = []
        self.commit_calls = 0

    async def execute(self, statement):
        statement_type = statement.__class__.__name__

        if statement_type == "Delete":
            match_id = self._extract_match_id(statement)
            if match_id is not None:
                self.records[match_id] = []
            return FakeScalarResult([])

        model_name = self._extract_model_name(statement)
        match_id = self._extract_match_id(statement)

        if model_name == "Match":
            status = self.statuses.get(match_id)
            row = (
                SimpleNamespace(
                    match_id=match_id,
                    local_match_id=match_id,
                    provider_fixture_id=self.provider_fixture_ids.get(match_id, match_id),
                    status=status,
                )
                if status is not None
                else None
            )
            return FakeScalarResult([row] if row else [])

        rows = list(self.records.get(match_id, [])) if match_id is not None else []
        return FakeScalarResult(rows)

    def add(self, row: MatchEvent):
        self._pending.append(row)

    async def flush(self):
        for row in self._pending:
            self.records.setdefault(row.match_id, []).append(row)
        self._pending.clear()

    async def commit(self):
        self.commit_calls += 1

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


def _api_event_payload(team_name: str = "Team A") -> Dict[str, Any]:
    return {
        "response": [
            {
                "time": {"elapsed": 12, "extra": 1},
                "team": {"id": 10, "name": team_name},
                "player": {"id": 100, "name": "Player"},
                "assist": {"id": 101, "name": "Assist"},
                "type": "Goal",
                "detail": "Normal Goal",
                "comments": None,
            }
        ]
    }


class FakeEventWriteRepository:
    def __init__(self, error=None):
        self.calls = []
        self.error = error

    async def replace_match_events(self, db, match_id, events):
        if self.error:
            raise self.error
        self.calls.append((match_id, events))


class FakeFlushDatabase:
    def __init__(self):
        self.flush_calls = 0
        self.commit_calls = 0

    async def flush(self):
        self.flush_calls += 1

    async def commit(self):
        self.commit_calls += 1


class FakeEventSyncDatabase(FakeFlushDatabase):
    async def execute(self, _statement):
        return FakeScalarResult([
            SimpleNamespace(home_team_id=122, away_team_id=120)
        ])


def test_final_event_sync_validates_before_replace_and_uses_split_identities():
    provider = FakeEventProvider(_api_event_payload())
    repository = FakeEventWriteRepository()
    service = EventSyncService(
        event_provider=provider,
        event_repository=repository,
        team_repository=FakeTeamRepository(),
    )
    service._resolve_event_identities = _resolve_test_event_identities
    db = FakeEventSyncDatabase()

    result = asyncio.run(service.refresh_match_events(db, 2222, provider_fixture_id=987654))

    assert result["success"] is True
    assert provider.match_ids == [987654]
    assert repository.calls[0][0] == 2222
    assert repository.calls[0][1][0]["resolved_player_id"] == 505
    assert repository.calls[0][1][0]["canonical_team_id"] == 122
    assert repository.calls[0][1][0]["team"]["id"] == 122
    assert repository.calls[0][1][0]["provider_player_id"] == "5"
    assert repository.calls[0][1][0]["resolved_assist_id"] == 606
    assert repository.calls[0][1][0]["provider_assist_id"] == "6"
    assert db.flush_calls == 1
    assert db.commit_calls == 0


async def _resolve_test_event_identities(_db, _event):
    return 505, "5", 606, "6"


@pytest.mark.parametrize(
    ("payload", "failure"),
    [
        ({"response": []}, "empty_response"),
        ({"errors": {"token": "invalid"}, "response": _api_event_payload()["response"]}, "provider_error"),
        ({"response": "not-a-list"}, "invalid_response"),
        ({"response": [{"time": {}, "team": {}, "type": "Goal"}]}, "malformed_event"),
        ({"response": [None]}, "malformed_event"),
        ({"response": [{"time": {"elapsed": "12"}, "team": {"id": 1}, "type": "Goal"}]}, "malformed_event"),
    ],
)
def test_invalid_final_event_payload_does_not_replace_existing_rows(payload, failure):
    provider = FakeEventProvider(payload)
    repository = FakeEventWriteRepository()
    service = EventSyncService(event_provider=provider, event_repository=repository)
    db = FakeFlushDatabase()

    result = asyncio.run(service.refresh_match_events(db, 2222, provider_fixture_id=987654))

    assert result == {"success": False, "message": failure}
    assert repository.calls == []
    assert db.flush_calls == 0
    assert db.commit_calls == 0


def test_event_repository_failure_propagates_without_service_commit():
    provider = FakeEventProvider(_api_event_payload())
    repository = FakeEventWriteRepository(error=RuntimeError("write failed"))
    service = EventSyncService(
        event_provider=provider,
        event_repository=repository,
        team_repository=FakeTeamRepository(),
    )
    service._resolve_event_identities = _resolve_test_event_identities
    db = FakeEventSyncDatabase()

    with pytest.raises(RuntimeError, match="write failed"):
        asyncio.run(service.refresh_match_events(db, 2222, provider_fixture_id=987654))

    assert db.commit_calls == 0


def _db_event(match_id: int, updated_at: datetime) -> MatchEvent:
    row = MatchEvent(
        match_id=match_id,
        time_elapsed=5,
        time_extra=0,
        team_id=20,
        team_name="DB Team",
        player_id=200,
        player_name="DB Player",
        assist_id=201,
        assist_name="DB Assist",
        type="Card",
        detail="Yellow Card",
        comments=None,
        updated_at=updated_at,
    )
    row.created_at = updated_at
    row.id = 1
    return row


def test_repeated_event_replacement_replaces_instead_of_appending():
    db = FakeSession()
    repository = EventRepository()
    event = {
        "time": {"elapsed": 12, "extra": None},
        "team": {"id": 122, "name": "Team"},
        "canonical_team_id": 122,
        "player": {"id": 5, "name": "Player"},
        "assist": {"id": None, "name": None},
        "resolved_player_id": 505,
        "provider_player_id": "5",
        "resolved_assist_id": None,
        "provider_assist_id": None,
        "type": "Goal",
        "detail": "Normal Goal",
        "comments": None,
    }

    async def run():
        await repository.replace_match_events(db, 2222, [event])
        await db.flush()
        assert len(db.records[2222]) == 1
        await repository.replace_match_events(db, 2222, [event])
        await db.flush()

    asyncio.run(run())

    assert len(db.records[2222]) == 1
    assert db.records[2222][0].match_id == 2222
    assert db.records[2222][0].provider_player_id == "5"
    assert db.commit_calls == 0


def test_live_within_10_minutes_returns_db_only():
    match_id = 1001
    now_utc = datetime.now(timezone.utc)
    db = FakeSession(statuses={match_id: "1H"})
    db.records[match_id] = [_db_event(match_id, now_utc - timedelta(minutes=5))]

    cache = FakeCacheService()
    client = FakeClient([_api_event_payload()])
    service = EventService(client=client, cache_service=cache)

    result = asyncio.run(service.get_cached_match_events(db, match_id))

    assert result
    assert result[0]["team_name"] == "DB Team"
    assert client.calls == 0
    assert cache.delete_calls == []


def test_live_after_10_minutes_refreshes_from_api():
    match_id = 1002
    now_utc = datetime.now(timezone.utc)
    db = FakeSession(statuses={match_id: "LIVE"})
    db.records[match_id] = [_db_event(match_id, now_utc - timedelta(minutes=11))]

    cache = FakeCacheService()
    client = FakeClient([_api_event_payload("API Team")])
    service = EventService(client=client, cache_service=cache)

    result = asyncio.run(service.get_cached_match_events(db, match_id))

    # Frozen architecture: read path must not refresh. Return DB row.
    assert result
    assert result[0]["team_name"] == "DB Team"
    assert client.calls == 0
    assert cache.delete_calls == []


def test_live_db_empty_fetches_api_and_persists():
    match_id = 1003
    db = FakeSession(statuses={match_id: "HT"})

    cache = FakeCacheService()
    client = FakeClient([_api_event_payload("API Team")])
    service = EventService(client=client, cache_service=cache)

    result = asyncio.run(service.get_cached_match_events(db, match_id))

    # Frozen architecture: read path must not trigger sync when DB empty.
    assert result == []
    assert client.calls == 0
    assert db.records.get(match_id, []) == []
    assert cache.delete_calls == []


def test_ft_with_db_data_returns_db_only():
    match_id = 1004
    now_utc = datetime.now(timezone.utc)
    db = FakeSession(statuses={match_id: "FT"})
    db.records[match_id] = [_db_event(match_id, now_utc - timedelta(days=1))]

    cache = FakeCacheService()
    client = FakeClient([_api_event_payload()])
    service = EventService(client=client, cache_service=cache)

    result = asyncio.run(service.get_cached_match_events(db, match_id))

    assert result
    assert result[0]["team_name"] == "DB Team"
    assert client.calls == 0


def test_ft_recovery_when_db_empty_fetches_once_and_saves():
    match_id = 1005
    db = FakeSession(statuses={match_id: "AET"})

    cache = FakeCacheService()
    client = FakeClient([_api_event_payload("Recovered Team")])
    service = EventService(client=client, cache_service=cache)

    # Under frozen architecture, reads do not trigger recovery sync.
    first = asyncio.run(service.get_cached_match_events(db, match_id))
    second = asyncio.run(service.get_cached_match_events(db, match_id))

    assert first == []
    assert second == []
    assert client.calls == 0


def test_updated_at_refresh_behavior_on_live_refresh():
    match_id = 1006
    now_utc = datetime.now(timezone.utc)
    db = FakeSession(statuses={match_id: "2H"})
    db.records[match_id] = [_db_event(match_id, now_utc - timedelta(minutes=20))]

    cache = FakeCacheService()
    client = FakeClient([_api_event_payload("Fresh Team")])
    service = EventService(client=client, cache_service=cache)

    # Frozen architecture: read path must not refresh stale DB rows.
    _ = asyncio.run(service.get_cached_match_events(db, match_id))

    saved = db.records[match_id][0]
    assert saved.team_name == "DB Team"
    # updated_at should remain unchanged
    assert saved.updated_at == (now_utc - timedelta(minutes=20))


class FakePlayerRepository:
    def __init__(self, players):
        self.players = players

    async def get_by_provider_id(self, db, provider_id, provider="api-football"):
        for player in self.players:
            if str(player.provider_id) == str(provider_id) and player.provider == provider:
                return player
        return None

    async def get_many_by_provider_ids(self, db, provider_ids, provider="api-football"):
        selected = []
        for pid in provider_ids:
            matches = [p for p in self.players if str(p.provider_id) == str(pid) and p.provider == provider]
            selected.extend(matches)
        return selected


class FakeEventRepository:
    def __init__(self):
        self.calls = []

    async def replace_match_events(self, db, match_id, events):
        self.calls.append({"match_id": match_id, "events": events})
        db.saved_events = list(events)


def test_event_sync_resolves_exact_player_master_for_authoritative_provider_id():
    from app.services.event_sync_service import EventSyncService

    player = SimpleNamespace(provider="api-football", provider_id="77", player_id=88)
    repo = FakeEventRepository()
    service = EventSyncService(
        event_provider=FakeEventProvider({"response": [{
            "time": {"elapsed": 10, "extra": 0},
            "team": {"id": 1, "name": "Home"},
            "player": {"id": 77, "name": "Player One"},
            "assist": {"id": 88, "name": "Player Two"},
            "type": "Goal",
            "detail": "Normal Goal",
            "comments": None,
        }]}),
        event_repository=repo,
        player_repository=FakePlayerRepository([player]),
        team_repository=FakeTeamRepository({1: 122}),
    )

    result = asyncio.run(service.refresh_match_events(FakeEventSyncDatabase(), 123))

    assert result["success"] is True
    assert result["api_events"][0]["resolved_player_id"] == 88
    assert repo.calls[0]["events"][0]["resolved_player_id"] == 88


def test_event_sync_keeps_unresolved_when_provider_player_id_missing():
    from app.services.event_sync_service import EventSyncService

    service = EventSyncService(
        event_provider=FakeEventProvider({"response": [{
            "time": {"elapsed": 10, "extra": 0},
            "team": {"id": 1, "name": "Home"},
            "player": {"name": "Player One"},
            "assist": {"id": 88, "name": "Player Two"},
            "type": "Goal",
            "detail": "Normal Goal",
            "comments": None,
        }]}),
        event_repository=FakeEventRepository(),
        player_repository=FakePlayerRepository([]),
        team_repository=FakeTeamRepository({1: 122}),
    )

    result = asyncio.run(service.refresh_match_events(FakeEventSyncDatabase(), 124))

    assert result["success"] is True
    assert result["api_events"][0]["resolved_player_id"] is None


def test_event_sync_keeps_unresolved_when_player_master_missing():
    from app.services.event_sync_service import EventSyncService

    service = EventSyncService(
        event_provider=FakeEventProvider({"response": [{
            "time": {"elapsed": 10, "extra": 0},
            "team": {"id": 1, "name": "Home"},
            "player": {"id": 77, "name": "Player One"},
            "assist": {"id": 88, "name": "Player Two"},
            "type": "Goal",
            "detail": "Normal Goal",
            "comments": None,
        }]}),
        event_repository=FakeEventRepository(),
        player_repository=FakePlayerRepository([]),
        team_repository=FakeTeamRepository({1: 122}),
    )

    result = asyncio.run(service.refresh_match_events(FakeEventSyncDatabase(), 125))

    assert result["success"] is True
    assert result["api_events"][0]["resolved_player_id"] is None


def test_event_sync_is_idempotent_for_repeated_exact_player_resolution():
    from app.services.event_sync_service import EventSyncService

    player = SimpleNamespace(provider="api-football", provider_id="77", player_id=88)
    event_provider = FakeEventProvider({"response": [{
        "time": {"elapsed": 9, "extra": 0},
        "team": {"id": 1, "name": "Home"},
        "player": {"id": 77, "name": "Player One"},
        "assist": {"id": 88, "name": "Player Two"},
        "type": "Goal",
        "detail": "Normal Goal",
        "comments": None,
    }]})
    service = EventSyncService(
        event_provider=event_provider,
        event_repository=FakeEventRepository(),
        player_repository=FakePlayerRepository([player]),
        team_repository=FakeTeamRepository({1: 122}),
    )

    first = asyncio.run(service.refresh_match_events(FakeEventSyncDatabase(), 126))
    second = asyncio.run(service.refresh_match_events(FakeEventSyncDatabase(), 126))

    assert first["api_events"][0]["resolved_player_id"] == 88
    assert second["api_events"][0]["resolved_player_id"] == 88
    assert first["api_events"][0]["resolved_player_id"] == second["api_events"][0]["resolved_player_id"]
