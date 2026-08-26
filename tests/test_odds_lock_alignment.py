import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import app.services.scheduler as scheduler_module
from app.services.scheduler import LiveUpdateScheduler


class Result:
    def __init__(self, rows=None, first_row=None):
        self.rows = rows or []
        self.first_row = first_row

    def all(self):
        return self.rows

    def first(self):
        return self.first_row


class DB:
    def __init__(self):
        self.commit_calls = 0
        self.rollback_calls = 0
        self.queries = 0

    async def execute(self, _statement):
        self.queries += 1
        if self.queries == 1:
            return Result(rows=[(100, "NS", datetime.now(timezone.utc))])
        return Result(first_row=None)

    async def commit(self):
        self.commit_calls += 1

    async def rollback(self):
        self.rollback_calls += 1


class Session:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, exc_type, exc, tb):
        return False


class Cache:
    def __init__(self, fail=False):
        self.fail = fail
        self.deleted = []

    async def delete(self, key):
        if self.fail:
            raise RuntimeError("cache unavailable")
        self.deleted.append(key)


def test_odds_scheduler_uses_fixture_lock_before_provider(monkeypatch):
    scheduler = LiveUpdateScheduler()
    db = DB()
    cache = Cache()
    calls = []

    async def fake_refresh(*args):
        calls.append("provider")
        return {"updated": 1}

    async def fake_lock(_db, resource_type, resource_identity, operation):
        calls.append((resource_type, resource_identity))
        return True, await operation()

    scheduler.cache_service = cache
    monkeypatch.setattr(scheduler_module, "async_session", lambda: Session(db))
    monkeypatch.setattr(scheduler_module, "run_with_resource_lock", fake_lock)
    monkeypatch.setattr(scheduler_module.football_service.odds_sync_service, "refresh_odds", fake_refresh)

    result = asyncio.run(scheduler._refresh_odds_job())

    assert result["refreshed_matches"] == 1
    assert calls == [("odds", 100), "provider"]
    assert db.commit_calls == 1
    assert cache.deleted == ["fover:match:100:odds"]


def test_odds_scheduler_does_not_call_provider_when_lock_conflicts(monkeypatch):
    scheduler = LiveUpdateScheduler()
    db = DB()
    provider_calls = []

    async def fake_refresh(*args):
        provider_calls.append(True)
        return {"updated": 1}

    async def unavailable_lock(*_args):
        return False, None

    monkeypatch.setattr(scheduler_module, "async_session", lambda: Session(db))
    monkeypatch.setattr(scheduler_module, "run_with_resource_lock", unavailable_lock)
    monkeypatch.setattr(scheduler_module.football_service.odds_sync_service, "refresh_odds", fake_refresh)

    result = asyncio.run(scheduler._refresh_odds_job())

    assert result["refreshed_matches"] == 0
    assert result["skipped_matches"] == 1
    assert provider_calls == []
    assert db.commit_calls == 0


def test_odds_cache_failure_does_not_rollback_after_commit(monkeypatch):
    scheduler = LiveUpdateScheduler()
    db = DB()
    cache = Cache(fail=True)

    async def fake_refresh(*args):
        return {"updated": 1}

    async def fake_lock(_db, resource_type, resource_identity, operation):
        assert (resource_type, resource_identity) == ("odds", 100)
        return True, await operation()

    scheduler.cache_service = cache
    monkeypatch.setattr(scheduler_module, "async_session", lambda: Session(db))
    monkeypatch.setattr(scheduler_module, "run_with_resource_lock", fake_lock)
    monkeypatch.setattr(scheduler_module.football_service.odds_sync_service, "refresh_odds", fake_refresh)

    result = asyncio.run(scheduler._refresh_odds_job())

    assert result["refreshed_matches"] == 1
    assert db.commit_calls == 1
    assert db.rollback_calls == 0
