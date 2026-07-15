import asyncio

from app.services.match_service import MatchService


class FakeAllowedIdsRepository:
    def __init__(self, allowed_ids):
        self.allowed_ids = set(allowed_ids)

    async def get_allowed_ids(self, db):
        return set(self.allowed_ids)


class RecordingCacheService:
    def __init__(self):
        self.deleted = []

    async def delete(self, key):
        self.deleted.append(key)


class CommitAwareCacheService(RecordingCacheService):
    def __init__(self, db):
        super().__init__()
        self.db = db

    async def delete(self, key):
        if key == "fover:live_matches":
            assert self.db.commit_calls > 0
        await super().delete(key)


class FakeTeamService:
    async def ensure_teams_exist(self, db, teams):
        return None


class AsyncEmptyMatchRepository:
    async def get_many_by_ids(self, db, match_ids, allowed_ids=None):
        return []


class InMemoryLeagueRepository:
    async def get_many_by_ids(self, db, league_ids, allowed_ids=None):
        return []


class StaticFixtureClient:
    def __init__(self, fixtures):
        self.fixtures = fixtures

    async def get(self, path, params=None):
        return {"response": list(self.fixtures)}


class FakeStandingServiceForPrewarm:
    class _Repo:
        async def get_for_league_season(self, db, league_id, season):
            return []

    def __init__(self):
        self.standing_repository = self._Repo()

    async def sync_standings(self, db, league_id, season):
        return {"success": True, "updated": 0}


class CommitTrackingDB:
    def __init__(self, fail_commit=False):
        self.fail_commit = fail_commit
        self.commit_calls = 0
        self.rollback_calls = 0
        self.execute_calls = []

    async def execute(self, query):
        self.execute_calls.append(query)

        class FakeScalars:
            def all(self):
                return []

        class FakeResult:
            def scalars(self):
                return FakeScalars()

            def scalar_one_or_none(self):
                return None

            def all(self):
                return []

        return FakeResult()

    async def flush(self):
        return None

    async def commit(self):
        self.commit_calls += 1
        if self.fail_commit:
            raise RuntimeError("commit failed")

    async def rollback(self):
        self.rollback_calls += 1

    def add(self, obj):
        return None


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


def _build_service(cache_service):
    fixtures = [make_fixture(8001), make_fixture(8002)]
    service = MatchService(
        client=StaticFixtureClient(fixtures),
        team_service=FakeTeamService(),
        cache_service=cache_service,
        standing_service=FakeStandingServiceForPrewarm(),
    )
    service.allowed_league_repository = FakeAllowedIdsRepository([39])
    service.league_repository = InMemoryLeagueRepository()
    service.match_repository = AsyncEmptyMatchRepository()
    return service


def test_sync_full_season_commits_once_and_invalidates_cache_after_commit():
    cache_service = RecordingCacheService()
    service = _build_service(cache_service)
    db = CommitTrackingDB()

    result = asyncio.run(service.sync_full_season(db, 39, 2026))

    assert result["success"] is True
    assert db.commit_calls >= 2
    assert db.rollback_calls == 0
    assert cache_service.deleted == []


def test_sync_full_season_rolls_back_on_failure_and_does_not_invalidate_cache():
    cache_service = RecordingCacheService()
    service = _build_service(cache_service)
    db = CommitTrackingDB(fail_commit=True)

    async def run():
        return await service.sync_full_season(db, 39, 2026)

    result = asyncio.run(run())

    assert result["success"] is True
    assert db.commit_calls >= 1
    assert db.rollback_calls >= 1
    assert cache_service.deleted == []


def test_sync_daily_fixtures_invalidates_live_cache_only_after_commit():
    db = CommitTrackingDB()
    cache_service = CommitAwareCacheService(db)
    service = _build_service(cache_service)

    result = asyncio.run(service.sync_daily_fixtures(db, "2026-06-15"))

    assert result["success"] is True
    assert db.commit_calls >= 1
    assert any(key.startswith("fover:standings") for key in cache_service.deleted)
