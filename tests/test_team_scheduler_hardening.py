import asyncio
import logging

import app.services.scheduler as scheduler_module
from app.services.scheduler import LiveUpdateScheduler


class FakeSchedulerDB:
    def __init__(self):
        self.commit_calls = 0
        self.rollback_calls = 0

    async def commit(self):
        self.commit_calls += 1

    async def rollback(self):
        self.rollback_calls += 1


class FakeAsyncSessionContext:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, exc_type, exc, tb):
        return False


def test_live_sync_skips_when_advisory_lock_not_acquired(monkeypatch, caplog):
    scheduler = LiveUpdateScheduler()
    db = FakeSchedulerDB()

    async def fake_acquire(_db):
        return False

    async def fake_release(_db):
        raise AssertionError("Release should not run when lock was never acquired")

    async def fake_should_sync(_db):
        raise AssertionError("Live sync gate should not run when lock is unavailable")

    monkeypatch.setattr(scheduler, "_acquire_live_match_sync_lock", fake_acquire)
    monkeypatch.setattr(scheduler, "_release_live_match_sync_lock", fake_release)
    monkeypatch.setattr(scheduler, "_should_sync_live_matches", fake_should_sync)
    monkeypatch.setattr(scheduler_module, "async_session", lambda: FakeAsyncSessionContext(db))

    with caplog.at_level(logging.INFO):
        asyncio.run(scheduler._sync_live_matches_job())

    assert "LIVE_SYNC_SKIPPED reason=lock_not_acquired" in caplog.text


def test_live_sync_releases_lock_on_early_return(monkeypatch):
    scheduler = LiveUpdateScheduler()
    db = FakeSchedulerDB()
    released = {"count": 0}

    async def fake_acquire(_db):
        return True

    async def fake_release(_db):
        released["count"] += 1

    async def fake_should_sync(_db):
        return False

    monkeypatch.setattr(scheduler, "_acquire_live_match_sync_lock", fake_acquire)
    monkeypatch.setattr(scheduler, "_release_live_match_sync_lock", fake_release)
    monkeypatch.setattr(scheduler, "_should_sync_live_matches", fake_should_sync)
    monkeypatch.setattr(scheduler_module, "async_session", lambda: FakeAsyncSessionContext(db))

    asyncio.run(scheduler._sync_live_matches_job())

    assert released["count"] == 1


def test_daily_fixture_sync_skips_when_advisory_lock_not_acquired(monkeypatch, caplog):
    scheduler = LiveUpdateScheduler()
    db = FakeSchedulerDB()

    async def fake_acquire(_db):
        return False

    async def fake_release(_db):
        raise AssertionError("Release should not run when lock was never acquired")

    async def fake_sync_daily(_db, _target_date):
        raise AssertionError("Daily sync should not run when lock is unavailable")

    monkeypatch.setattr(scheduler, "_acquire_daily_fixture_sync_lock", fake_acquire)
    monkeypatch.setattr(scheduler, "_release_daily_fixture_sync_lock", fake_release)
    monkeypatch.setattr(scheduler_module.football_service, "sync_daily_fixtures", fake_sync_daily)
    monkeypatch.setattr(scheduler_module, "async_session", lambda: FakeAsyncSessionContext(db))

    with caplog.at_level(logging.INFO):
        asyncio.run(scheduler._sync_daily_fixtures_job())

    assert "DAILY_FIXTURE_SYNC_SKIPPED reason=lock_not_acquired" in caplog.text


def test_repair_daily_matches_skips_when_advisory_lock_not_acquired(monkeypatch, caplog):
    scheduler = LiveUpdateScheduler()
    db = FakeSchedulerDB()

    async def fake_acquire(_db):
        return False

    async def fake_release(_db):
        raise AssertionError("Release should not run when lock was never acquired")

    async def fake_sync_daily(_db, _target_date):
        raise AssertionError("Repair sync should not run when lock is unavailable")

    monkeypatch.setattr(scheduler, "_acquire_repair_daily_matches_lock", fake_acquire)
    monkeypatch.setattr(scheduler, "_release_repair_daily_matches_lock", fake_release)
    monkeypatch.setattr(scheduler_module.football_service, "sync_daily_fixtures", fake_sync_daily)
    monkeypatch.setattr(scheduler_module, "async_session", lambda: FakeAsyncSessionContext(db))

    with caplog.at_level(logging.INFO):
        asyncio.run(scheduler._repair_daily_matches_job())

    assert "REPAIR_DAILY_MATCHES_SKIPPED reason=lock_not_acquired" in caplog.text
