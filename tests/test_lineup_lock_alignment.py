import asyncio
from contextlib import asynccontextmanager

import app.services.scheduler as scheduler_module
from app.services.scheduler import LiveUpdateScheduler


class DB:
    def __init__(self):
        self.commit_calls = 0
        self.rollback_calls = 0

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
    def __init__(self):
        self.deleted = []

    async def delete(self, key):
        self.deleted.append(key)



def setup_scheduler(monkeypatch, db, calls, lock_result=True):
    scheduler = LiveUpdateScheduler()
    scheduler.cache_service = Cache()

    async def candidates(_db, now_utc=None, window_minutes=90):
        return [100]

    async def cooldown(*args, **kwargs):
        return False

    async def touch(*args, **kwargs):
        return None

    async def fake_sync(_db, match_id):
        calls.append(("provider", match_id))
        return {"success": True, "created": False, "updated": True}

    async def fake_lock(_db, resource_type, resource_identity, operation):
        calls.append((resource_type, resource_identity))
        if not lock_result:
            return False, None
        return True, await operation()

    scheduler.lineup_refresh_state_repository.is_on_cooldown = cooldown
    scheduler.lineup_refresh_state_repository.touch = touch
    monkeypatch.setattr(scheduler, "_get_lineup_refresh_candidates", candidates)
    monkeypatch.setattr(scheduler_module, "async_session", lambda: Session(db))
    monkeypatch.setattr(scheduler_module, "run_with_resource_lock", fake_lock)
    monkeypatch.setattr(scheduler_module.football_service, "sync_match_lineup", fake_sync)
    return scheduler


def test_lineup_scheduler_locks_before_provider_and_invalidates_after_commit(monkeypatch):
    db = DB()
    calls = []
    scheduler = setup_scheduler(monkeypatch, db, calls)

    result = asyncio.run(scheduler._refresh_lineups_job())

    assert result["synced_matches"] == 1
    assert calls == [("lineup", 100), ("provider", 100)]
    assert db.commit_calls == 1
    assert db.rollback_calls == 0
    assert scheduler.cache_service.deleted == ["fover:lineup:100"]


def test_lineup_scheduler_skips_provider_when_lock_conflicts(monkeypatch):
    db = DB()
    calls = []
    scheduler = setup_scheduler(monkeypatch, db, calls, lock_result=False)

    result = asyncio.run(scheduler._refresh_lineups_job())

    assert result["synced_matches"] == 0
    assert result["skipped_matches"] == 1
    assert calls == [("lineup", 100)]
    assert db.commit_calls == 0
    assert db.rollback_calls == 0
