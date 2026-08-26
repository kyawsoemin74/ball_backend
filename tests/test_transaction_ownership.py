import ast
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from app.api import matches
from app.api import ads
from app.api import leagues
from app.models.ad_config import AdConfig
from app.schemas.ad_config import AdConfigUpdateRequest
from app.repositories.ad_repository import AdRepository
from app.services.venue_sync_service import VenueSyncService
from sqlalchemy.exc import IntegrityError


def test_repositories_do_not_own_transactions():
    repository_root = Path(__file__).parents[1] / "app" / "repositories"
    forbidden = {"commit", "rollback"}

    for path in repository_root.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr in forbidden:
                raise AssertionError(f"repository transaction ownership violation: {path}:{node.lineno}")


class _AdDb:
    def __init__(self):
        self.events = []

    def add(self, config):
        self.events.append("add")

    async def flush(self):
        self.events.append("flush")

    async def refresh(self, config):
        self.events.append("refresh")

    async def commit(self):
        self.events.append("commit")


def test_ad_repository_flushes_without_commit():
    db = _AdDb()
    config = AdConfig(is_enabled=True)

    result = asyncio.run(AdRepository().update_current_config(db, config))

    assert result is config
    assert db.events == ["add", "flush", "refresh"]


class _Db:
    def __init__(self):
        self.events = []

    async def commit(self):
        self.events.append("commit")

    async def rollback(self):
        self.events.append("rollback")


class _LineupService:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error

    async def sync_match_lineup(self, db, match_id):
        if self.error:
            raise self.error
        return self.result


class _CallerService:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error

    async def _result(self, *args, **kwargs):
        if self.error:
            raise self.error
        return self.result

    sync_match_events = _result
    sync_match_statistics = _result
    sync_full_season = _result
    sync_daily_fixtures = _result
    sync_all_leagues = _result
    sync_standings = _result


class _Cache:
    def __init__(self, events):
        self.events = events

    async def delete(self, key):
        self.events.append("cache")


class _VenueRaceDb:
    def __init__(self):
        self.pending = ["match-write"]
        self.events = []

    @asynccontextmanager
    async def begin_nested(self):
        self.events.append("savepoint-begin")
        try:
            yield self
        except IntegrityError:
            self.events.append("savepoint-rollback")
            raise
        else:
            self.events.append("savepoint-release")


class _VenueRaceRepository:
    def __init__(self):
        self.lookup_count = 0
        self.upsert_count = 0

    async def get_by_provider_id(self, db, provider_id, provider):
        self.lookup_count += 1
        if self.lookup_count == 1:
            return None
        return type("Venue", (), {"venue_id": 99})()

    async def upsert_one(self, db, venue_data):
        self.upsert_count += 1
        if self.upsert_count == 1:
            raise IntegrityError("duplicate", None, None)
        return type("Venue", (), {"venue_id": 99})()


def test_venue_conflict_uses_savepoint_and_preserves_parent_work():
    db = _VenueRaceDb()
    service = VenueSyncService()
    service.repository = _VenueRaceRepository()

    result = asyncio.run(service.sync_fixture_payload(db, {"id": 42, "name": "Arena"}))

    assert result["success"] is True
    assert result["venue_id"] == 99
    assert db.pending == ["match-write"]
    assert db.events == ["savepoint-begin", "savepoint-rollback"]


def _run_lineup_route(monkeypatch, result=None, error=None):
    db = _Db()
    events = db.events
    monkeypatch.setattr(matches, "football_service", _LineupService(result=result, error=error))
    monkeypatch.setattr(matches, "CacheService", lambda: _Cache(events))

    async def unlocked(db, resource_type, resource_identity, operation):
        return True, await operation()

    monkeypatch.setattr(matches, "run_with_resource_lock", unlocked)
    return db, asyncio.run(matches.sync_match_lineup_route(123, db))


def test_manual_lineup_success_commits_before_cache_invalidation(monkeypatch):
    db, result = _run_lineup_route(monkeypatch, {"success": True})

    assert result == {"success": True}
    assert db.events == ["commit", "cache"]


def test_manual_lineup_failure_rolls_back_without_cache_invalidation(monkeypatch):
    db, result = _run_lineup_route(monkeypatch, {"success": False, "reason": "unavailable"})

    assert result["success"] is False
    assert db.events == ["rollback"]


def test_manual_lineup_exception_rolls_back_without_cache_invalidation(monkeypatch):
    db = _Db()
    events = db.events
    monkeypatch.setattr(matches, "football_service", _LineupService(error=RuntimeError("provider")))
    monkeypatch.setattr(matches, "CacheService", lambda: _Cache(events))

    async def unlocked(db, resource_type, resource_identity, operation):
        return True, await operation()

    monkeypatch.setattr(matches, "run_with_resource_lock", unlocked)

    try:
        asyncio.run(matches.sync_match_lineup_route(123, db))
    except RuntimeError:
        pass
    else:
        raise AssertionError("lineup exception should propagate")

    assert db.events == ["rollback"]


async def _unlocked(db, resource_type, resource_identity, operation):
    return True, await operation()


def test_affected_match_callers_gate_commit_on_success(monkeypatch):
    monkeypatch.setattr(matches, "run_with_resource_lock", _unlocked)
    for route, method_name, args in (
        (matches.sync_match_events, "sync_match_events", (123,)),
        (matches.sync_match_statistics_route, "sync_match_statistics", (123,)),
        (matches.sync_full_season, "sync_full_season", ()),
        (matches.sync_daily_matches, "sync_daily_fixtures", ("2026-08-25",)),
    ):
        db = _Db()
        service = _CallerService(result={"success": False, "reason": "provider"})
        monkeypatch.setattr(matches, "football_service", service)
        if method_name == "sync_full_season":
            result = asyncio.run(route(db=db, league_id=39, season=2026))
        elif method_name == "sync_daily_fixtures":
            from datetime import date
            result = asyncio.run(route(date_val=date(2026, 8, 25), db=db))
        else:
            result = asyncio.run(route(*args, db=db))
        assert result["success"] is False
        assert db.events == ["rollback"]


def test_affected_league_callers_gate_commit_on_success(monkeypatch):
    monkeypatch.setattr(leagues, "run_with_resource_lock", _unlocked)
    for route, args in (
        (leagues.sync_all_leagues, ()),
        (leagues.sync_league_standings, (39,)),
    ):
        db = _Db()
        monkeypatch.setattr(leagues, "football_service", _CallerService(result={"success": False}))
        if args:
            result = asyncio.run(route(*args, db=db))
        else:
            result = asyncio.run(route(db=db))
        assert result["success"] is False
        assert db.events == ["rollback"]


def test_affected_callers_commit_success_before_cache(monkeypatch):
    monkeypatch.setattr(matches, "run_with_resource_lock", _unlocked)
    for route, args in (
        (matches.sync_match_events, (123,)),
        (matches.sync_match_statistics_route, (123,)),
    ):
        db = _Db()
        monkeypatch.setattr(matches, "football_service", _CallerService(result={"success": True}))
        monkeypatch.setattr(matches, "CacheService", lambda: _Cache(db.events))
        result = asyncio.run(route(*args, db=db))
        assert result["success"] is True
        assert db.events == ["commit", "cache"]


class _AdService:
    async def update_current_config(self, db, config):
        return AdConfig(id=1, is_enabled=config.is_enabled)


def test_ad_config_commits_before_cache_invalidation(monkeypatch):
    db = _Db()
    monkeypatch.setattr(ads, "AdMobService", _AdService)
    monkeypatch.setattr(ads, "CacheService", lambda: _Cache(db.events))

    result = asyncio.run(ads.update_ad_config(AdConfigUpdateRequest(is_enabled=True), db))

    assert result.id == 1
    assert db.events == ["commit", "cache"]