import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Dict, List

from app.cache import make_cache_key
from app.models.match_event import MatchEvent
from app.services.event_service import EventService


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
    def __init__(self, statuses: Dict[int, str] | None = None):
        self.statuses = statuses or {}
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
            row = SimpleNamespace(match_id=match_id, status=status) if status is not None else None
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

    assert result
    assert result[0]["team_name"] == "API Team"
    assert client.calls == 1
    assert cache.delete_calls == [make_cache_key("match", match_id, "events")]
    assert db.commit_calls == 1


def test_live_db_empty_fetches_api_and_persists():
    match_id = 1003
    db = FakeSession(statuses={match_id: "HT"})

    cache = FakeCacheService()
    client = FakeClient([_api_event_payload("API Team")])
    service = EventService(client=client, cache_service=cache)

    result = asyncio.run(service.get_cached_match_events(db, match_id))

    assert result
    assert client.calls == 1
    assert len(db.records[match_id]) == 1
    assert cache.delete_calls == [make_cache_key("match", match_id, "events")]
    assert db.commit_calls == 1


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

    first = asyncio.run(service.get_cached_match_events(db, match_id))
    second = asyncio.run(service.get_cached_match_events(db, match_id))

    assert first
    assert second
    assert first[0]["team_name"] == "Recovered Team"
    assert second[0]["team_name"] == "Recovered Team"
    assert client.calls == 1
    assert db.commit_calls == 1


def test_updated_at_refresh_behavior_on_live_refresh():
    match_id = 1006
    now_utc = datetime.now(timezone.utc)
    db = FakeSession(statuses={match_id: "2H"})
    db.records[match_id] = [_db_event(match_id, now_utc - timedelta(minutes=20))]

    cache = FakeCacheService()
    client = FakeClient([_api_event_payload("Fresh Team")])
    service = EventService(client=client, cache_service=cache)

    _ = asyncio.run(service.get_cached_match_events(db, match_id))

    saved = db.records[match_id][0]
    assert saved.team_name == "Fresh Team"
    assert saved.updated_at is not None
    assert (datetime.now(timezone.utc) - saved.updated_at) < timedelta(minutes=1)
    assert db.commit_calls == 1
