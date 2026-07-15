import asyncio

from app.services.match_service import MatchService


class FakeAllowedIdsRepository:
    def __init__(self, allowed_ids):
        self.allowed_ids = set(allowed_ids)

    async def get_allowed_ids(self, db):
        return set(self.allowed_ids)


class FakeCacheService:
    async def delete(self, key):
        return None


class FakeTeamService:
    async def ensure_teams_exist(self, db, teams):
        return None


class AsyncEmptyMatchRepository:
    async def get_many_by_ids(self, db, match_ids, allowed_ids=None):
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


class InMemoryLeagueRepository:
    async def get_many_by_ids(self, db, league_ids, allowed_ids=None):
        return []


class FakeSyncDB:
    def __init__(self):
        self.execute_calls = []
        self.commit_calls = 0
        self.rollback_calls = 0

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

    async def rollback(self):
        self.rollback_calls += 1

    def add(self, obj):
        return None


class ExistingMatchRepository:
    def __init__(self, existing_ids):
        self.existing_ids = set(existing_ids)

    async def get_many_by_ids(self, db, match_ids, allowed_ids=None):
        return [type("Row", (), {"match_id": mid})() for mid in match_ids if mid in self.existing_ids]


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


def test_sync_full_season_uses_on_conflict_upsert_statement():
    fixtures = [make_fixture(7001), make_fixture(7002)]
    service = MatchService(
        client=StaticFixtureClient(fixtures),
        team_service=FakeTeamService(),
        cache_service=FakeCacheService(),
        standing_service=FakeStandingServiceForPrewarm(),
    )
    service.allowed_league_repository = FakeAllowedIdsRepository([39])
    service.league_repository = InMemoryLeagueRepository()
    service.match_repository = AsyncEmptyMatchRepository()
    db = FakeSyncDB()

    result = asyncio.run(service.sync_full_season(db, 39, 2026))

    assert result["success"] is True
    assert result["inserted"] == 2
    assert result["updated"] == 0

    sql_texts = [str(stmt) for stmt in db.execute_calls]
    assert any("ON CONFLICT" in text for text in sql_texts)


def test_sync_full_season_inserted_updated_summary_unchanged_with_existing_rows():
    fixtures = [make_fixture(7101), make_fixture(7102), make_fixture(7103)]
    service = MatchService(
        client=StaticFixtureClient(fixtures),
        team_service=FakeTeamService(),
        cache_service=FakeCacheService(),
        standing_service=FakeStandingServiceForPrewarm(),
    )
    service.allowed_league_repository = FakeAllowedIdsRepository([39])
    service.league_repository = InMemoryLeagueRepository()
    service.match_repository = ExistingMatchRepository(existing_ids={7102})
    db = FakeSyncDB()

    result = asyncio.run(service.sync_full_season(db, 39, 2026))

    assert result["success"] is True
    assert result["inserted"] == 2
    assert result["updated"] == 1
    assert result["total"] == 3
