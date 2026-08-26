import asyncio

import app.services.scheduler as scheduler_module
from app.services.scheduler import LiveUpdateScheduler


class Result:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class DB:
    def __init__(self):
        self.commit_calls = 0
        self.rollback_calls = 0

    async def execute(self, _statement):
        return Result("1H")

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


class ActiveMatches:
    async def get_active_matches(self):
        return [100]


class Cache:
    def __init__(self, fail=False):
        self.fail = fail
        self.deleted = []

    async def delete(self, key):
        if self.fail:
            raise RuntimeError("cache unavailable")
        self.deleted.append(key)


def configure(monkeypatch, db, calls, lock_result=True, cache=None):
    scheduler = LiveUpdateScheduler()
    scheduler.cache_service = cache or Cache()

    async def fake_lock(_db, resource_type, resource_identity, operation):
        calls.append((resource_type, resource_identity))
        if not lock_result:
            return False, None
        return True, await operation()

    async def fake_sync(_db, match_id):
        calls.append(("provider", match_id))
        return {"success": True, "match_id": match_id}

    monkeypatch.setattr(scheduler_module, "active_match_service", ActiveMatches())
    monkeypatch.setattr(scheduler_module, "async_session", lambda: Session(db))
    monkeypatch.setattr(scheduler_module, "run_with_resource_lock", fake_lock)
    monkeypatch.setattr(scheduler_module.football_service, "sync_match_events", fake_sync)
    monkeypatch.setattr(scheduler, "_should_refresh_match_events", lambda *_args: asyncio.sleep(0, result=True))
    return scheduler


def test_events_scheduler_locks_before_provider_and_invalidates_after_commit(monkeypatch):
    db = DB()
    calls = []
    scheduler = configure(monkeypatch, db, calls)

    result = asyncio.run(scheduler._refresh_events_job())

    assert result["synced_matches"] == 1
    assert calls == [("events", 100), ("provider", 100)]
    assert db.commit_calls == 1
    assert db.rollback_calls == 0
    assert scheduler.cache_service.deleted == ["fover:match:100:events"]


def test_events_scheduler_skips_provider_when_lock_conflicts(monkeypatch):
    db = DB()
    calls = []
    scheduler = configure(monkeypatch, db, calls, lock_result=False)

    result = asyncio.run(scheduler._refresh_events_job())

    assert result["synced_matches"] == 0
    assert result["skipped_matches"] == 1
    assert calls == [("events", 100)]
    assert db.commit_calls == 0
    assert db.rollback_calls == 0


def test_events_cache_failure_does_not_rollback_after_commit(monkeypatch):
    db = DB()
    calls = []
    scheduler = configure(monkeypatch, db, calls, cache=Cache(fail=True))

    result = asyncio.run(scheduler._refresh_events_job())

    assert result["synced_matches"] == 1
    assert db.commit_calls == 1
    assert db.rollback_calls == 0
