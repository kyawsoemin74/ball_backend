import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

import app.services.fixture_sync_service as fixture_sync_module
from app.models.match_lineup_finalization import MatchLineupFinalization
from app.services.fixture_sync_service import FixtureSyncService
from app.services.lineup_sync_service import LineupSyncService


class FakeResult:
    def __init__(self, row=None, rows=None):
        self.row = row
        self.rows = rows or []

    def scalar_one_or_none(self):
        return self.row

    def scalars(self):
        return self

    def all(self):
        return self.rows


class FinalizationDB:
    def __init__(self, record=None, fail_commit=False):
        self.record = record
        self.fail_commit = fail_commit
        self.commit_calls = 0
        self.rollback_calls = 0
        self.flush_calls = 0
        self.cache_committed = False

    async def execute(self, statement, params=None):
        return FakeResult(row=self.record)

    async def flush(self):
        self.flush_calls += 1

    async def commit(self):
        self.commit_calls += 1
        if self.fail_commit:
            raise RuntimeError("commit failed")
        self.cache_committed = True

    async def rollback(self):
        self.rollback_calls += 1

    def add(self, record):
        self.record = record


class FinalizationRepository:
    def __init__(self, record):
        self.record = record
        self.attempts = 0
        self.retryable = []
        self.successes = 0

    async def get_by_match_id(self, db, match_id):
        return self.record

    async def mark_attempt_started(self, db, record, attempted_at):
        self.attempts += 1
        record.attempt_count += 1
        record.last_attempted_at = attempted_at
        return record

    async def mark_retryable(self, db, record, category, reason, attempted_at):
        self.retryable.append((category, reason))
        record.status = "RETRYABLE"
        record.failure_category = category
        record.failure_reason = reason
        return record

    async def mark_success(self, db, record, completed_at):
        self.successes += 1
        record.status = "SUCCESS"
        record.completed_at = completed_at
        return record


class FakeLineupSyncService:
    async def sync_lineup(self, db, match_id, **kwargs):
        return {"success": True, "match_id": match_id, "created": False, "updated": True}


class FakeStandingService:
    class Repo:
        async def get_for_league_season(self, db, league_id, season):
            return []

    standing_repository = Repo()


class FakeTeamService:
    async def resolve_provider_teams(self, db, teams):
        return {"resolved": {int(item["provider_id"]): int(item["provider_id"]) for item in teams}, "unresolved": []}


class FakeMatchRepository:
    def __init__(self, status):
        self.status = status

    async def get_many_by_ids(self, db, match_ids, allowed_ids=None):
        return [SimpleNamespace(match_id=match_ids[0], status=self.status)]


class FakeAllowedRepository:
    async def get_allowed_ids(self, db):
        return {39}


class FakeLeagueRepository:
    async def find_by_provider_identity(self, db, provider, provider_id):
        return SimpleNamespace(league_id=39)


class FakeFixtureService(FixtureSyncService):
    async def _finalize_terminal_match_events(self, db, match_id, status):
        return True


class FixtureDB:
    async def execute(self, statement, params=None):
        return FakeResult(rows=[])

    async def flush(self):
        return None

    def add(self, record):
        return None


def make_fixture(match_id, status):
    return {
        "fixture": {
            "id": match_id,
            "date": "2026-06-15T18:00:00+00:00",
            "status": {"short": status, "elapsed": 90},
            "venue": {"name": "A", "city": "B"},
        },
        "league": {"id": 39, "season": 2026, "name": "Allowed", "country": "X"},
        "teams": {
            "home": {"id": 1, "name": "Home"},
            "away": {"id": 2, "name": "Away"},
        },
        "goals": {"home": 1, "away": 0},
    }


def build_fixture_service(previous_status):
    service = FakeFixtureService(
        client=SimpleNamespace(),
        team_service=FakeTeamService(),
        standing_service=FakeStandingService(),
    )
    service.allowed_league_repository = FakeAllowedRepository()
    service.league_repository = FakeLeagueRepository()
    service.match_repository = FakeMatchRepository(previous_status)
    service.team_sync_service = SimpleNamespace(update_team_context=_noop, sync_team_coach=_noop)
    service.venue_sync_service = SimpleNamespace(sync_fixture_payload=_venue)
    service.referee_sync_service = SimpleNamespace(sync_fixture_referee=_referee)
    service.final_lineup_finalization_repository = SimpleNamespace(
        create_required=_create_required,
        get_retry_candidates=_empty_retry_candidates,
    )
    return service


async def _noop(*args, **kwargs):
    return None


async def _venue(*args, **kwargs):
    return {"venue_id": None}


async def _referee(*args, **kwargs):
    return {"referee_id": None}


async def _create_required(db, match_id, required_at=None):
    return MatchLineupFinalization(match_id=match_id, status="REQUIRED")


async def _empty_retry_candidates(db, limit):
    return []


def test_terminal_transitions_create_final_lineup_candidates():
    for status in ("FT", "AET", "PEN"):
        service = build_fixture_service("LIVE")
        result, _ = asyncio.run(service._process_sync_with_candidates(FixtureDB(), [make_fixture(100, status)]))
        assert result["final_lineup_candidates"] == [100]


def test_repeated_terminal_status_does_not_create_final_lineup_candidate():
    service = build_fixture_service("FT")
    result, _ = asyncio.run(service._process_sync_with_candidates(FixtureDB(), [make_fixture(100, "FT")]))
    assert result["final_lineup_candidates"] == []


def test_terminal_lineup_sync_requires_explicit_override():
    class Provider:
        calls = 0

        async def get_match_lineup(self, match_id):
            self.calls += 1
            return {"response": []}

    provider = Provider()
    service = LineupSyncService(lineup_provider=provider)
    db = FinalizationDB()
    service.lineup_repository = SimpleNamespace(
        get_match_status=lambda db, match_id: _terminal_match(),
    )

    normal = asyncio.run(service.sync_lineup(db, 1, validate_lineup=lambda value: True))
    final = asyncio.run(service.sync_lineup(db, 1, validate_lineup=lambda value: False, allow_terminal_status=True))

    assert normal["skipped"] is True
    assert final["success"] is False
    assert provider.calls == 1


async def _terminal_match():
    return SimpleNamespace(status="FT")


def test_final_lineup_success_commits_before_cache(monkeypatch):
    record = MatchLineupFinalization(match_id=1, status="REQUIRED", attempt_count=0)
    repository = FinalizationRepository(record)
    db = FinalizationDB(record)
    cache_events = []

    async def fake_sync(*args, **kwargs):
        return {"success": True, "match_id": 1}

    async def fake_delete(key):
        assert db.commit_calls == 1
        cache_events.append(key)

    monkeypatch.setattr(fixture_sync_module, "async_session", lambda: _session(db))
    monkeypatch.setattr(fixture_sync_module, "run_with_resource_lock", _lock)
    service = FixtureSyncService(client=SimpleNamespace(), team_service=FakeTeamService())
    service.final_lineup_finalization_repository = repository
    service.cache_service = SimpleNamespace(delete=fake_delete)

    async def run():
        from app.services import football as football_module
        monkeypatch.setattr(football_module.football_service, "sync_match_lineup", fake_sync)
        return await service.finalize_pending_lineups([1])

    result = asyncio.run(run())
    assert result["succeeded"] == 1
    assert record.status == "SUCCESS"
    assert cache_events == ["fover:lineup:1"]


def test_final_lineup_provider_failure_records_retryable_without_cache(monkeypatch):
    record = MatchLineupFinalization(match_id=1, status="REQUIRED", attempt_count=0)
    repository = FinalizationRepository(record)
    db = FinalizationDB(record)
    cache = SimpleNamespace(delete=_unexpected_cache_delete)

    async def fake_sync(*args, **kwargs):
        return {"success": False, "match_id": 1, "reason": "lineup_not_available"}

    monkeypatch.setattr(fixture_sync_module, "async_session", lambda: _session(db))
    monkeypatch.setattr(fixture_sync_module, "run_with_resource_lock", _lock)
    service = FixtureSyncService(client=SimpleNamespace(), team_service=FakeTeamService())
    service.final_lineup_finalization_repository = repository
    service.cache_service = cache

    async def run():
        from app.services import football as football_module
        monkeypatch.setattr(football_module.football_service, "sync_match_lineup", fake_sync)
        return await service.finalize_pending_lineups([1])

    result = asyncio.run(run())
    assert result["failed"] == 1
    assert record.status == "RETRYABLE"
    assert record.failure_category == "INVALID_RESPONSE"
    assert db.rollback_calls == 1
    assert db.commit_calls == 1


def test_commit_ambiguity_preserves_success_and_invalidates_cache(monkeypatch):
    record = MatchLineupFinalization(match_id=1, status="REQUIRED", attempt_count=0)
    repository = FinalizationRepository(record)
    db = FinalizationDB(fail_commit=True)
    cache_events = []

    async def fake_sync(*args, **kwargs):
        return {"success": True, "match_id": 1}

    async def commit_then_raise():
        db.commit_calls += 1
        db.cache_committed = True
        raise RuntimeError("commit outcome unknown")

    db.commit = commit_then_raise

    async def fake_delete(key):
        cache_events.append(key)

    monkeypatch.setattr(fixture_sync_module, "async_session", lambda: _session(db))
    monkeypatch.setattr(fixture_sync_module, "run_with_resource_lock", _lock)
    service = FixtureSyncService(client=SimpleNamespace(), team_service=FakeTeamService())
    service.final_lineup_finalization_repository = repository
    service.cache_service = SimpleNamespace(delete=fake_delete)

    async def run():
        from app.services import football as football_module
        monkeypatch.setattr(football_module.football_service, "sync_match_lineup", fake_sync)
        return await service.finalize_pending_lineups([1])

    result = asyncio.run(run())
    assert result["succeeded"] == 1
    assert result["failed"] == 0
    assert record.status == "SUCCESS"
    assert cache_events == ["fover:lineup:1"]


async def _unexpected_cache_delete(key):
    raise AssertionError("cache must not be invalidated after a failed final lineup")


@asynccontextmanager
async def _session(db):
    yield db


async def _lock(db, resource_type, resource_identity, operation):
    assert resource_type == "lineup"
    assert resource_identity == 1
    return True, await operation()
