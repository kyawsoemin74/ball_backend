import asyncio
from contextlib import asynccontextmanager
from datetime import date

import app.api.matches as matches_api
import app.services.scheduler as scheduler_module
from app.services.scheduler import LiveUpdateScheduler


class FakeDB:
    def __init__(self):
        self.commit_calls = 0
        self.rollback_calls = 0

    async def commit(self):
        self.commit_calls += 1

    async def rollback(self):
        self.rollback_calls += 1


class FakeCache:
    async def delete(self, _key):
        return None


class FakeAsyncSession:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, exc_type, exc, tb):
        return False



def test_date_api_uses_global_fixture_lock_before_sync(monkeypatch):
    db = FakeDB()
    calls = []

    async def fake_sync_daily_fixtures(**kwargs):
        calls.append("provider_sync")
        return {"success": True}

    async def fake_lock(_db, resource_type, resource_identity, operation):
        calls.append((resource_type, resource_identity))
        return True, await operation()

    monkeypatch.setattr(matches_api.football_service, "sync_daily_fixtures", fake_sync_daily_fixtures)
    monkeypatch.setattr(matches_api, "run_with_resource_lock", fake_lock)
    monkeypatch.setattr(matches_api, "CacheService", lambda: FakeCache())

    result = asyncio.run(matches_api.sync_daily_matches(date(2026, 8, 25), db))

    assert result == {"success": True}
    assert calls == [("fixture_query", "global"), "provider_sync"]
    assert db.commit_calls == 1



def test_season_api_skips_provider_when_global_lock_is_unavailable(monkeypatch):
    db = FakeDB()
    provider_calls = []

    async def fake_sync_full_season(**kwargs):
        provider_calls.append(True)
        return {"success": True}

    async def unavailable_lock(*_args):
        return False, None

    monkeypatch.setattr(matches_api.football_service, "sync_full_season", fake_sync_full_season)
    monkeypatch.setattr(matches_api, "run_with_resource_lock", unavailable_lock)

    try:
        asyncio.run(matches_api.sync_full_season(39, 2026, db))
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 409
    else:
        raise AssertionError("Expected conflict when fixture lock is unavailable")

    assert provider_calls == []
    assert db.commit_calls == 0
    assert db.rollback_calls == 0



def test_live_scheduler_uses_global_fixture_lock(monkeypatch):
    scheduler = LiveUpdateScheduler()
    db = FakeDB()
    calls = []

    async def fake_should_sync(_db):
        return True

    async def fake_sync_live(_db):
        calls.append("provider_sync")
        return {"success": True, "updated": 0}

    async def fake_lock(_db, resource_type, resource_identity, operation):
        calls.append((resource_type, resource_identity))
        return True, await operation()

    monkeypatch.setattr(scheduler, "_should_sync_live_matches", fake_should_sync)
    monkeypatch.setattr(scheduler_module.football_service, "sync_live_matches", fake_sync_live)
    monkeypatch.setattr(scheduler_module, "run_with_resource_lock", fake_lock)
    monkeypatch.setattr(scheduler_module, "async_session", lambda: FakeAsyncSession(db))
    monkeypatch.setattr(scheduler_module, "active_match_service", object())

    asyncio.run(scheduler._sync_live_matches_job())

    assert calls == [("fixture_query", "global"), "provider_sync"]
    assert db.commit_calls == 1



def test_repair_scheduler_uses_one_global_fixture_lock(monkeypatch):
    scheduler = LiveUpdateScheduler()
    db = FakeDB()
    calls = []

    async def fake_sync_daily(_db, target_date):
        calls.append(("provider_sync", target_date))
        return {"success": True}

    async def fake_lock(_db, resource_type, resource_identity, operation):
        calls.append((resource_type, resource_identity))
        return True, await operation()

    monkeypatch.setattr(scheduler_module.football_service, "sync_daily_fixtures", fake_sync_daily)
    monkeypatch.setattr(scheduler_module, "run_with_resource_lock", fake_lock)
    monkeypatch.setattr(scheduler_module, "async_session", lambda: FakeAsyncSession(db))
    asyncio.run(scheduler._repair_daily_matches_job())

    assert calls[0] == ("fixture_query", "global")
    assert [item[0] for item in calls[1:]] == ["provider_sync", "provider_sync"]
    assert db.commit_calls == 2


def test_repair_scheduler_rolls_back_failed_date_before_next_date(monkeypatch):
    scheduler = LiveUpdateScheduler()
    db = FakeDB()
    calls = []

    async def fake_sync_daily(_db, target_date):
        calls.append(target_date)
        return {"success": len(calls) == 2}

    async def fake_lock(_db, resource_type, resource_identity, operation):
        return True, await operation()

    monkeypatch.setattr(scheduler_module.football_service, "sync_daily_fixtures", fake_sync_daily)
    monkeypatch.setattr(scheduler_module, "run_with_resource_lock", fake_lock)
    monkeypatch.setattr(scheduler_module, "async_session", lambda: FakeAsyncSession(db))

    asyncio.run(scheduler._repair_daily_matches_job())

    assert len(calls) == 2
    assert db.rollback_calls == 1
    assert db.commit_calls == 1
