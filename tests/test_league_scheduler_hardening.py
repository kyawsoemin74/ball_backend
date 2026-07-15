import asyncio
from datetime import datetime, timedelta, timezone

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


def test_refresh_standings_releases_advisory_lock_when_pair_fetch_fails(monkeypatch):
    scheduler = LiveUpdateScheduler()
    db = FakeSchedulerDB()
    released = {"count": 0}

    async def fake_get_pairs(_db):
        raise RuntimeError("pair query failed")

    async def fake_acquire(_db):
        return True

    async def fake_release(_db):
        released["count"] += 1

    monkeypatch.setattr(scheduler, "_get_allowed_standings_pairs", fake_get_pairs)
    monkeypatch.setattr(scheduler, "_acquire_standings_refresh_lock", fake_acquire)
    monkeypatch.setattr(scheduler, "_release_standings_refresh_lock", fake_release)
    monkeypatch.setattr(scheduler_module, "async_session", lambda: FakeAsyncSessionContext(db))

    metrics = asyncio.run(scheduler._refresh_standings_job())

    assert metrics == {"processed_pairs": 0, "success_pairs": 0, "failed_pairs": 0}
    assert released["count"] == 1


def test_refresh_odds_continues_after_match_exception(monkeypatch):
    scheduler = LiveUpdateScheduler()
    db = FakeSchedulerDB()
    refresh_calls = []

    class FakeQueryResult:
        def __init__(self, rows):
            self._rows = rows

        def all(self):
            return self._rows

        def first(self):
            return self._rows[0] if self._rows else None

    class FakeSchedulerDBWithQuery(FakeSchedulerDB):
        def __init__(self):
            super().__init__()
            self._execute_count = 0

        async def execute(self, _query):
            self._execute_count += 1
            if self._execute_count == 1:
                return FakeQueryResult([(1, "NS", datetime.now(timezone.utc) + timedelta(hours=1)), (2, "NS", datetime.now(timezone.utc) + timedelta(hours=2)), (3, "NS", datetime.now(timezone.utc) + timedelta(hours=3))])
            return FakeQueryResult([])

    db = FakeSchedulerDBWithQuery()

    async def fake_refresh_odds(_db, fixture_id, _cache_key, _ttl):
        refresh_calls.append(fixture_id)
        if fixture_id == 2:
            raise RuntimeError("boom")
        return {"source": "api", "odds": [], "cached": False, "match_started": False, "updated": 0}

    monkeypatch.setattr(scheduler_module, "async_session", lambda: FakeAsyncSessionContext(db))
    monkeypatch.setattr(scheduler_module.football_service.odds_sync_service, "refresh_odds", fake_refresh_odds)

    metrics = asyncio.run(scheduler._refresh_odds_job())

    assert metrics["processed_matches"] == 3
    assert metrics["refreshed_matches"] == 2
    assert metrics["failed_matches"] == 1
    assert refresh_calls == [1, 2, 3]


def test_refresh_odds_skips_fixture_outside_72_hour_window(monkeypatch):
    scheduler = LiveUpdateScheduler()
    refresh_calls = []

    class FakeQueryResult:
        def __init__(self, rows):
            self._rows = rows

        def all(self):
            return self._rows

        def first(self):
            return self._rows[0] if self._rows else None

    class FakeSchedulerDBWithWindow(FakeSchedulerDB):
        def __init__(self):
            super().__init__()
            self._execute_count = 0

        async def execute(self, _query):
            self._execute_count += 1
            if self._execute_count == 1:
                return FakeQueryResult([(999, "NS", datetime.now(timezone.utc) + timedelta(hours=100))])
            return FakeQueryResult([])

    db = FakeSchedulerDBWithWindow()

    async def fake_refresh_odds(_db, fixture_id, _cache_key, _ttl):
        refresh_calls.append(fixture_id)
        return {"source": "api", "odds": [], "cached": False, "match_started": False, "updated": 0}

    monkeypatch.setattr(scheduler_module, "async_session", lambda: FakeAsyncSessionContext(db))
    monkeypatch.setattr(scheduler_module.football_service.odds_sync_service, "refresh_odds", fake_refresh_odds)

    metrics = asyncio.run(scheduler._refresh_odds_job())

    assert refresh_calls == []
    assert metrics["eligible_matches"] == 0
    assert metrics["processed_matches"] == 0
    assert metrics["refreshed_matches"] == 0


def test_refresh_odds_skips_fresh_snapshot(monkeypatch):
    scheduler = LiveUpdateScheduler()
    refresh_calls = []

    class FakeQueryResult:
        def __init__(self, rows):
            self._rows = rows

        def all(self):
            return self._rows

        def first(self):
            return self._rows[0] if self._rows else None

    class FakeSchedulerDBWithFreshSnapshot(FakeSchedulerDB):
        def __init__(self):
            super().__init__()
            self._execute_count = 0

        async def execute(self, _query):
            self._execute_count += 1
            if self._execute_count == 1:
                return FakeQueryResult([(1, "NS", datetime.now(timezone.utc) + timedelta(hours=2))])
            return FakeQueryResult([(datetime.now(timezone.utc) - timedelta(hours=2),)])

    db = FakeSchedulerDBWithFreshSnapshot()

    async def fake_refresh_odds(_db, fixture_id, _cache_key, _ttl):
        refresh_calls.append(fixture_id)
        return {"source": "api", "odds": [], "cached": False, "match_started": False, "updated": 0}

    monkeypatch.setattr(scheduler_module, "async_session", lambda: FakeAsyncSessionContext(db))
    monkeypatch.setattr(scheduler_module.football_service.odds_sync_service, "refresh_odds", fake_refresh_odds)

    metrics = asyncio.run(scheduler._refresh_odds_job())

    assert refresh_calls == []
    assert metrics["processed_matches"] == 1
    assert metrics["skipped_matches"] == 1
    assert metrics["refreshed_matches"] == 0
