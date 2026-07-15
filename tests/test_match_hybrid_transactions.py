import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import app.services.fixture_sync_service as fixture_sync_module
from app.services.fixture_sync_service import FixtureSyncService


class RecordingCacheService:
    def __init__(self):
        self.deleted = []

    async def delete(self, key):
        self.deleted.append(key)


class RecordingTeamSyncService:
    def __init__(self):
        self.calls = []

    async def update_team_context(self, db, team_id, *, current_league_id=None, current_season=None):
        self.calls.append(
            {
                "team_id": team_id,
                "current_league_id": current_league_id,
                "current_season": current_season,
            }
        )


class FakeAllowedLeagueRepository:
    async def get_allowed_ids(self, db):
        return {39}


class FakeLeagueRepository:
    async def get_many_by_ids(self, db, league_ids, allowed_ids=None):
        return []


class FakeMatchRepository:
    async def get_many_by_ids(self, db, match_ids, allowed_ids=None):
        return []


class FakeTeamService:
    def __init__(self, fail_for_team_ids=None):
        self.fail_for_team_ids = set(fail_for_team_ids or [])

    async def ensure_teams_exist(self, db, teams):
        if any(team.get("team_id") in self.fail_for_team_ids for team in teams):
            raise RuntimeError("team ensure failed")
        return None


class FakeStandingService:
    def __init__(self):
        self.standing_repository = SimpleNamespace(get_for_league_season=self._get_for_league_season)
        self._defer_standings_cache_invalidation = False

    async def _get_for_league_season(self, db, league_id, season):
        return []

    async def sync_standings(self, db, league_id, season):
        return {"success": True, "updated": 0}


class RecordingActiveMatchService:
    def __init__(self):
        self.marked = []
        self.removed = []

    async def mark_match_active(self, match_id):
        self.marked.append(match_id)
        return 300

    async def remove_match_active(self, match_id):
        self.removed.append(match_id)


class FakeDB:
    def __init__(self, fail_flush=False):
        self.fail_flush = fail_flush
        self.commit_calls = 0
        self.rollback_calls = 0
        self.flush_calls = 0
        self.execute_calls = 0

    async def execute(self, stmt):
        self.execute_calls += 1
        return None

    async def flush(self):
        self.flush_calls += 1
        if self.fail_flush and self.flush_calls == 1:
            raise RuntimeError("flush failed")

    async def commit(self):
        self.commit_calls += 1

    async def rollback(self):
        self.rollback_calls += 1

    def add(self, obj):
        return None

    @asynccontextmanager
    async def begin_nested(self):
        yield self


class StaticFixtureClient:
    def __init__(self, fixtures):
        self.fixtures = fixtures

    async def get(self, path, params=None):
        return {"response": list(self.fixtures)}


def make_fixture(fixture_id, league_id=39, season=2026):
    return {
        "fixture": {
            "id": fixture_id,
            "date": "2026-06-15T18:00:00+00:00",
            "status": {"short": "NS", "elapsed": 0},
            "venue": {"name": "A", "city": "B"},
        },
        "league": {"id": league_id, "season": season, "name": "Allowed League", "country": "X", "logo": None, "flag": None},
        "teams": {
            "home": {"id": fixture_id * 10 + 1, "name": f"Home{fixture_id}", "logo": None},
            "away": {"id": fixture_id * 10 + 2, "name": f"Away{fixture_id}", "logo": None},
        },
        "goals": {"home": 0, "away": 0},
    }


def build_service(fixtures, team_service, cache_service=None):
    service = FixtureSyncService(
        client=StaticFixtureClient(fixtures),
        team_service=team_service,
        cache_service=cache_service or RecordingCacheService(),
        standing_service=FakeStandingService(),
    )
    service.allowed_league_repository = FakeAllowedLeagueRepository()
    service.league_repository = FakeLeagueRepository()
    service.match_repository = FakeMatchRepository()
    service.team_sync_service = RecordingTeamSyncService()
    return service


def test_sync_daily_fixtures_continues_after_team_dependency_failure():
    fixtures = [make_fixture(2001), make_fixture(2002)]
    team_service = FakeTeamService(fail_for_team_ids={20011})
    service = build_service(fixtures, team_service)
    db = FakeDB()

    result = asyncio.run(service.sync_daily_fixtures(db, "2026-06-15"))

    assert result["success"] is True
    assert result["inserted"] == 1
    assert result["failed"] == 1
    assert result["total"] == 2


def test_sync_daily_fixtures_continues_after_flush_failure():
    fixtures = [make_fixture(3001), make_fixture(3002)]
    service = build_service(fixtures, FakeTeamService())
    db = FakeDB(fail_flush=True)

    result = asyncio.run(service.sync_daily_fixtures(db, "2026-06-15"))

    assert result["success"] is True
    assert result["inserted"] == 1
    assert result["failed"] == 1
    assert result["total"] == 2


def test_sync_daily_fixtures_registers_live_matches_for_scheduler(monkeypatch):
    fixtures = [make_fixture(4001)]
    fixtures[0]["fixture"]["status"]["short"] = "1H"
    service = build_service(fixtures, FakeTeamService())
    db = FakeDB()
    fake_active_service = RecordingActiveMatchService()
    monkeypatch.setattr(fixture_sync_module, "active_match_service", fake_active_service)

    result = asyncio.run(service.sync_daily_fixtures(db, "2026-06-15"))

    assert result["success"] is True
    assert fake_active_service.marked == [4001]
    assert fake_active_service.removed == []


def test_sync_daily_fixtures_removes_terminal_matches_from_scheduler(monkeypatch):
    fixtures = [make_fixture(5001)]
    fixtures[0]["fixture"]["status"]["short"] = "FT"
    service = build_service(fixtures, FakeTeamService())
    db = FakeDB()
    fake_active_service = RecordingActiveMatchService()
    monkeypatch.setattr(fixture_sync_module, "active_match_service", fake_active_service)

    result = asyncio.run(service.sync_daily_fixtures(db, "2026-06-15"))

    assert result["success"] is True
    assert fake_active_service.marked == []
    assert fake_active_service.removed == [5001]
