import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute

import app.api.matches as matches_api
import app.services.final_match_sync_service as final_match_sync_module
import app.services.scheduler as scheduler_module
from app.services.final_match_sync_service import FinalMatchSyncService
from app.services.scheduler import LiveUpdateScheduler


def fixture_payload(status):
    return {
        "fixture": {"id": 7001, "status": {"short": status, "elapsed": 95}},
        "goals": {"home": 3, "away": 2},
    }


class FinalizationRecord:
    def __init__(self):
        self.state = "RUNNING"
        self.completed_at = None


class FinalizationRepository:
    def __init__(self):
        self.record = FinalizationRecord()

    async def get_by_match_id(self, _db, _match_id, **_kwargs):
        return self.record

    async def mark_success(self, _db, record, completed_at):
        record.state = "SUCCESS"
        record.completed_at = completed_at


class MatchRepository:
    def __init__(self):
        self.match = SimpleNamespace(
            local_match_id=123,
            provider="api-football",
            provider_fixture_id=7001,
        )
        self.updates = []

    async def get_by_id(self, _db, _match_id):
        return self.match

    async def update_live_state(self, _db, match_id, **state):
        self.updates.append((match_id, state))
        for key, value in state.items():
            setattr(self.match, key, value)
        return True


class FixtureProvider:
    async def get_fixtures_by_ids(self, _fixture_ids):
        return {"response": [fixture_payload("FT")]}


class EventSync:
    def __init__(self, result=None, error=None):
        self.result = result or {"success": True}
        self.error = error
        self.calls = []

    async def sync_match_events(self, _db, match_id):
        self.calls.append(match_id)
        if self.error:
            raise self.error
        return self.result


class Database:
    async def flush(self):
        return None

    async def commit(self):
        return None

    async def rollback(self):
        return None


def make_finalizer(event_sync, status="FT"):
    provider = FixtureProvider()
    provider.get_fixtures_by_ids = AsyncMock(
        return_value={"response": [fixture_payload(status)]}
    )
    matches = MatchRepository()
    finalizations = FinalizationRepository()
    service = FinalMatchSyncService(
        provider,
        match_repository=matches,
        finalization_repository=finalizations,
        event_sync_service=event_sync,
    )
    return service, matches, finalizations, provider


@pytest.mark.parametrize("status", ["FT", "AET", "PEN"])
def test_terminal_finalization_syncs_events_before_success(status, monkeypatch):
    events = EventSync()
    service, _matches, finalizations, _provider = make_finalizer(events, status)
    locks = []

    async def with_lock(_db, resource_type, resource_id, operation):
        locks.append((resource_type, resource_id))
        return True, await operation()

    monkeypatch.setattr(final_match_sync_module, "run_with_resource_lock", with_lock)

    result = asyncio.run(service.sync_final_match(Database(), 123))

    assert result["state"] == "SUCCESS"
    assert result["final_status"] == status
    assert events.calls == [123]
    assert locks == [("events", 123)]
    assert finalizations.record.state == "SUCCESS"


def test_terminal_event_failure_does_not_complete_finalization():
    events = EventSync(result={"success": False, "message": "Events unavailable"})
    service, matches, finalizations, _provider = make_finalizer(events)

    result = asyncio.run(service.sync_final_match(Database(), 123))

    assert result["state"] == "FAILED"
    assert result["category"] == "FINAL_EVENT_SYNC_FAILURE"
    assert result["retryable"] is True
    assert finalizations.record.state == "RUNNING"
    assert matches.match.status == "FT"


def test_terminal_event_exception_does_not_complete_finalization():
    events = EventSync(error=TimeoutError("provider timeout"))
    service, _matches, finalizations, _provider = make_finalizer(events, "AET")

    result = asyncio.run(service.sync_final_match(Database(), 123))

    assert result["state"] == "FAILED"
    assert result["retryable"] is True
    assert finalizations.record.state == "RUNNING"


class SchedulerDatabase(Database):
    def __init__(self, status):
        self.status = status

    async def execute(self, _statement):
        return SimpleNamespace(scalar_one_or_none=lambda: self.status)


class ActiveMatches:
    async def get_active_matches(self):
        return [123]


class Session:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, *_args):
        return False


@pytest.mark.parametrize("status", ["NS", "FT", "AET", "PEN", "PST"])
def test_event_scheduler_does_not_refresh_non_live_matches(monkeypatch, status):
    db = SchedulerDatabase(status)
    scheduler = LiveUpdateScheduler()
    sync = AsyncMock()
    monkeypatch.setattr(scheduler_module, "active_match_service", ActiveMatches())
    monkeypatch.setattr(scheduler_module, "async_session", lambda: Session(db))
    monkeypatch.setattr(scheduler_module.football_service, "sync_match_events", sync)

    result = asyncio.run(scheduler._refresh_events_job())

    assert result["skipped_matches"] == 1
    assert result["synced_matches"] == 0
    sync.assert_not_awaited()


def test_terminal_finalization_invalidates_event_cache_after_commit():
    scheduler = LiveUpdateScheduler()
    cache = SimpleNamespace(delete=AsyncMock(return_value=True))
    scheduler.cache_service = cache

    asyncio.run(scheduler._invalidate_final_match_caches(123))

    keys = [call.args[0] for call in cache.delete.await_args_list]
    assert "fover:live_matches" in keys
    assert "fover:match:123:events" in keys


class MatchResult:
    def __init__(self, match):
        self.match = match

    def scalar_one_or_none(self):
        return self.match


class BackfillDatabase:
    def __init__(self, match):
        self.match = match
        self.commit_calls = 0
        self.rollback_calls = 0

    async def execute(self, _statement):
        return MatchResult(self.match)

    async def commit(self):
        self.commit_calls += 1

    async def rollback(self):
        self.rollback_calls += 1


class EventRepository:
    def __init__(self, events=None):
        self.events = events or []

    async def get_by_match_id(self, _db, _match_id):
        return self.events


class BackfillCache:
    def __init__(self):
        self.deleted = []

    async def delete(self, key):
        self.deleted.append(key)
        return True


def backfill_match(status="FT"):
    return SimpleNamespace(
        local_match_id=123,
        status=status,
        match_time=datetime.now(timezone.utc) - timedelta(days=1),
    )


def configure_backfill(monkeypatch, db, events=None):
    repository = EventRepository(events)
    cache = BackfillCache()
    sync = AsyncMock(return_value={"success": True, "count": 2})
    monkeypatch.setattr(matches_api.football_service, "sync_match_events", sync)
    monkeypatch.setattr(
        matches_api.football_service.event_service,
        "event_repository",
        repository,
    )
    monkeypatch.setattr(matches_api, "CacheService", lambda: cache)

    async def locked(_db, _resource, _identity, operation):
        return True, await operation()

    monkeypatch.setattr(matches_api, "run_with_resource_lock", locked)
    return repository, cache, sync


def test_explicit_admin_backfill_only_creates_first_historical_snapshot(monkeypatch):
    db = BackfillDatabase(backfill_match("PEN"))
    _repository, cache, sync = configure_backfill(monkeypatch, db)

    result = asyncio.run(
        matches_api.sync_match_events(
            123,
            operation="HISTORICAL_BACKFILL",
            db=db,
        )
    )

    assert result["operation"] == "HISTORICAL_BACKFILL"
    assert result["success"] is True
    assert db.commit_calls == 1
    sync.assert_awaited_once_with(db=db, match_id=123)
    assert cache.deleted == ["fover:match:123:events"]


def test_explicit_backfill_refuses_repair_of_existing_event_rows(monkeypatch):
    db = BackfillDatabase(backfill_match())
    _repository, _cache, sync = configure_backfill(monkeypatch, db, [object()])

    with pytest.raises(HTTPException) as error:
        asyncio.run(
            matches_api.sync_match_events(
                123,
                operation="HISTORICAL_BACKFILL",
                db=db,
            )
        )

    assert error.value.status_code == 409
    sync.assert_not_awaited()
    assert db.commit_calls == 0


@pytest.mark.parametrize("match_status", ["NS", "1H", "LIVE", "PST"])
def test_explicit_backfill_requires_finished_match(monkeypatch, match_status):
    db = BackfillDatabase(backfill_match(match_status))
    _repository, _cache, sync = configure_backfill(monkeypatch, db)

    with pytest.raises(HTTPException):
        asyncio.run(
            matches_api.sync_match_events(
                123,
                operation="HISTORICAL_BACKFILL",
                db=db,
            )
        )

    sync.assert_not_awaited()


def test_historical_backfill_retains_existing_admin_authorization():
    route = next(
        route
        for route in matches_api.router.routes
        if isinstance(route, APIRoute)
        and route.path == "/matches/sync/{match_id}/events"
    )

    assert any(
        dependency.call is matches_api.current_active_admin
        for dependency in route.dependant.dependencies
    )
