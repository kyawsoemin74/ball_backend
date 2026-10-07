import asyncio
from types import SimpleNamespace

import pytest

from app.models.match_event import MatchEvent
from app.repositories.event_repository import EventRepository
from app.services.event_service import EventService
from app.services.event_sync_service import EventSyncService


MATCH_ID = 7301
HOME_TEAM_ID = 122
AWAY_TEAM_ID = 120


class ScalarResult:
    def __init__(self, value=None):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class EventDatabase:
    def __init__(self):
        self.match = SimpleNamespace(
            home_team_id=HOME_TEAM_ID,
            away_team_id=AWAY_TEAM_ID,
        )
        self.events = []
        self.pending = []
        self.replacements = 0
        self.flush_calls = 0
        self.commit_calls = 0

    async def execute(self, statement):
        if statement.__class__.__name__ == "Delete":
            self.events = []
            self.replacements += 1
            return ScalarResult()

        descriptions = getattr(statement, "column_descriptions", [])
        if descriptions and descriptions[0].get("entity").__name__ == "Match":
            return ScalarResult(self.match)
        raise AssertionError(f"Unexpected statement: {statement}")

    def add(self, row):
        assert isinstance(row, MatchEvent)
        self.pending.append(row)

    async def flush(self):
        self.events.extend(self.pending)
        self.pending.clear()
        self.flush_calls += 1

    async def commit(self):
        self.commit_calls += 1


class EventProvider:
    def __init__(self, events):
        self.events = events
        self.fixture_ids = []

    async def get_match_events(self, fixture_id):
        self.fixture_ids.append(fixture_id)
        return {"response": self.events}


class TeamRepository:
    def __init__(self, mapping):
        self.mapping = mapping
        self.calls = []

    async def find_by_provider_identity(self, db, provider, provider_id):
        self.calls.append((provider, provider_id))
        team_id = self.mapping.get(provider_id)
        return SimpleNamespace(team_id=team_id) if team_id is not None else None


class RecordingEventRepository:
    def __init__(self):
        self.calls = []

    async def replace_match_events(self, db, match_id, events):
        self.calls.append((match_id, events))


def make_event(provider_team_id, event_type="Goal", elapsed=10):
    return {
        "time": {"elapsed": elapsed, "extra": None},
        "team": {"id": provider_team_id, "name": f"Provider {provider_team_id}"},
        "player": {"id": None, "name": None},
        "assist": {"id": None, "name": None},
        "type": event_type,
        "detail": None,
        "comments": None,
    }


def make_service(events, mapping, repository=None):
    team_repository = TeamRepository(mapping)
    event_repository = repository or RecordingEventRepository()
    service = EventSyncService(
        event_provider=EventProvider(events),
        event_repository=event_repository,
        team_repository=team_repository,
    )
    return service, team_repository, event_repository


def test_provider_team_id_resolves_to_canonical_id_before_repository():
    provider_team_id = 3
    service, team_repository, repository = make_service(
        [make_event(provider_team_id)],
        {provider_team_id: HOME_TEAM_ID},
    )

    result = asyncio.run(service.refresh_match_events(EventDatabase(), MATCH_ID))

    assert result["success"] is True
    assert team_repository.calls == [("api-football", provider_team_id)]
    event = repository.calls[0][1][0]
    assert event["canonical_team_id"] == HOME_TEAM_ID
    assert event["team"]["id"] == HOME_TEAM_ID
    assert event["team"]["id"] != provider_team_id
    assert result["api_events"][0]["team"]["id"] == HOME_TEAM_ID


@pytest.mark.parametrize(
    ("provider_team_id", "canonical_team_id"),
    [(3, HOME_TEAM_ID), (9, AWAY_TEAM_ID)],
)
def test_home_and_away_events_persist_the_canonical_match_team(
    provider_team_id,
    canonical_team_id,
):
    database = EventDatabase()
    service, _, _ = make_service(
        [make_event(provider_team_id)],
        {provider_team_id: canonical_team_id},
        EventRepository(),
    )

    result = asyncio.run(service.refresh_match_events(database, MATCH_ID))

    assert result["success"] is True
    assert database.events[0].team_id == canonical_team_id
    assert database.events[0].team_id in (HOME_TEAM_ID, AWAY_TEAM_ID)


def test_multiple_events_resolve_home_and_away_independently():
    events = [make_event(3, elapsed=7), make_event(9, elapsed=22)]
    database = EventDatabase()
    service, team_repository, _ = make_service(
        events,
        {3: HOME_TEAM_ID, 9: AWAY_TEAM_ID},
        EventRepository(),
    )

    result = asyncio.run(service.refresh_match_events(database, MATCH_ID))

    assert result["success"] is True
    assert team_repository.calls == [
        ("api-football", 3),
        ("api-football", 9),
    ]
    assert [event.team_id for event in database.events] == [HOME_TEAM_ID, AWAY_TEAM_ID]


@pytest.mark.parametrize("event_type", ["Goal", "Card", "subst", "Var", "Penalty"])
def test_supported_event_types_use_the_same_team_identity_resolution(event_type):
    provider_team_id = 73
    service, team_repository, repository = make_service(
        [make_event(provider_team_id, event_type)],
        {provider_team_id: HOME_TEAM_ID},
    )

    result = asyncio.run(service.refresh_match_events(EventDatabase(), MATCH_ID))

    assert result["success"] is True
    assert team_repository.calls == [("api-football", provider_team_id)]
    assert repository.calls[0][1][0]["canonical_team_id"] == HOME_TEAM_ID


def test_missing_team_identity_fails_before_replacing_snapshot():
    repository = RecordingEventRepository()
    service, team_repository, _ = make_service(
        [make_event(3), make_event(999, elapsed=11)],
        {3: HOME_TEAM_ID},
        repository,
    )

    result = asyncio.run(service.refresh_match_events(EventDatabase(), MATCH_ID))

    assert result == {"success": False, "message": "TEAM_IDENTITY_MISSING"}
    assert team_repository.calls == [
        ("api-football", 3),
        ("api-football", 999),
    ]
    assert repository.calls == []


def test_match_team_mismatch_fails_before_replacing_snapshot():
    repository = RecordingEventRepository()
    service, _, _ = make_service([make_event(3)], {3: 777}, repository)

    result = asyncio.run(service.refresh_match_events(EventDatabase(), MATCH_ID))

    assert result == {
        "success": False,
        "message": "EVENT_TEAM_IDENTITY_MISMATCH",
    }
    assert repository.calls == []


def test_repository_replacement_persists_canonical_id_only():
    database = EventDatabase()
    service, _, _ = make_service(
        [make_event(3)],
        {3: HOME_TEAM_ID},
        EventRepository(),
    )

    result = asyncio.run(
        service.refresh_match_events(database, MATCH_ID, provider_fixture_id=90003)
    )

    assert result["success"] is True
    assert database.events[0].team_id == HOME_TEAM_ID
    assert database.events[0].team_id != 3
    assert database.replacements == 1
    assert database.flush_calls == 1
    assert database.commit_calls == 0


def test_repository_rejects_event_without_explicit_canonical_team_id():
    database = EventDatabase()
    event = make_event(3)

    with pytest.raises(ValueError, match="canonical team ID"):
        asyncio.run(EventRepository().replace_match_events(database, MATCH_ID, [event]))

    assert database.replacements == 0
    assert database.pending == []


def test_event_service_serializes_persisted_canonical_team_id():
    event = MatchEvent(
        id=1,
        match_id=MATCH_ID,
        time_elapsed=10,
        time_extra=None,
        team_id=HOME_TEAM_ID,
        team_name="Home",
        player_id=None,
        provider_player_id=None,
        player_name=None,
        assist_id=None,
        provider_assist_id=None,
        assist_name=None,
        type="Goal",
        detail=None,
        comments=None,
    )

    response = EventService._serialize_db_events([event])

    assert response[0]["team_id"] == HOME_TEAM_ID


def test_repeated_canonical_event_sync_replaces_snapshot_instead_of_appending():
    database = EventDatabase()
    service, _, _ = make_service(
        [make_event(3)],
        {3: HOME_TEAM_ID},
        EventRepository(),
    )

    first = asyncio.run(service.refresh_match_events(database, MATCH_ID))
    second = asyncio.run(service.refresh_match_events(database, MATCH_ID))

    assert first["success"] is True
    assert second["success"] is True
    assert len(database.events) == 1
    assert database.events[0].team_id == HOME_TEAM_ID
    assert database.replacements == 2
