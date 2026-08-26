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

    async def resolve_provider_teams(self, db, teams):
        return {
            "resolved": {int(item["provider_id"]): int(item["provider_id"]) for item in teams},
            "unresolved": [],
            "total": len(teams),
        }


class AsyncEmptyMatchRepository:
    async def get_many_by_ids(self, db, match_ids, allowed_ids=None):
        return []


class InMemoryLeagueRepository:
    def __init__(self, existing_ids=None):
        self.existing_ids = set(existing_ids or {39})

    async def find_by_provider_identity(self, db, provider, provider_id):
        if int(provider_id) not in self.existing_ids:
            return None
        return type("LeagueRow", (), {"league_id": int(provider_id), "provider": provider, "provider_id": str(provider_id)})()

    async def get_many_by_ids(self, db, league_ids, allowed_ids=None):
        return [
            type("LeagueRow", (), {"league_id": league_id, "provider": "api-football", "provider_id": str(league_id)})()
            for league_id in league_ids if league_id in self.existing_ids
        ]


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

    async def execute(self, query, params=None):
        self.execute_calls.append(query)

        class FakeScalars:
            def all(self):
                return []

        class FakeResult:
            def scalars(self):
                return FakeScalars()

            def scalar_one_or_none(self):
                return None

            def scalar_one(self):
                return True

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


def test_sync_full_season_defers_commit_to_outer_owner():
    cache_service = RecordingCacheService()
    service = _build_service(cache_service)
    db = CommitTrackingDB()

    result = asyncio.run(service.sync_full_season(db, 39, 2026))

    assert result["success"] is True
    assert db.commit_calls == 0
    assert db.rollback_calls == 0
    assert cache_service.deleted == []


def test_sync_full_season_does_not_own_commit_or_rollback():
    cache_service = RecordingCacheService()
    service = _build_service(cache_service)
    db = CommitTrackingDB(fail_commit=True)

    async def run():
        return await service.sync_full_season(db, 39, 2026)

    result = asyncio.run(run())

    assert result["success"] is True
    assert db.commit_calls == 0
    assert db.rollback_calls == 0
    assert cache_service.deleted == []


def test_sync_daily_fixtures_leaves_commit_and_cache_invalidation_to_outer_owner():
    db = CommitTrackingDB()
    cache_service = CommitAwareCacheService(db)
    service = _build_service(cache_service)

    result = asyncio.run(service.sync_daily_fixtures(db, "2026-06-15"))

    assert result["success"] is True
    assert db.commit_calls == 0
    assert cache_service.deleted == []

    async def commit_outer_transaction():
        await db.commit()
        await cache_service.delete("fover:live_matches")

    asyncio.run(commit_outer_transaction())

    assert db.commit_calls == 1
    assert cache_service.deleted == ["fover:live_matches"]


def test_standing_prewarm_does_not_own_commit_or_cache_invalidation():
    db = CommitTrackingDB()
    cache_service = RecordingCacheService()
    service = _build_service(cache_service)

    result = asyncio.run(service.fixture_sync_service._prewarm_missing_standings(db, {(39, 2026)}))

    assert result["synced_pairs"] == 1
    assert db.commit_calls == 0
    assert db.rollback_calls == 0
    assert cache_service.deleted == []


def test_terminal_event_finalization_does_not_own_commit_or_cache_invalidation():
    db = CommitTrackingDB()
    cache_service = RecordingCacheService()
    service = _build_service(cache_service)

    completed = asyncio.run(
        service.fixture_sync_service._finalize_terminal_match_events(db, 8001, "FT")
    )

    assert completed is True
    assert db.commit_calls == 0
    assert db.rollback_calls == 0
    assert cache_service.deleted == []
