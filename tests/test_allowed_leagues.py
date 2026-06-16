import asyncio
import importlib
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from app.api.admin_leagues import router as admin_leagues_router
from app.core.security import get_current_active_admin
from app.db import get_db
from app.main import app
from app.repositories.allowed_league_repository import AllowedLeagueRepository
from app.services.allowed_league_service import AllowedLeagueService
from app.services.league_service import LeagueService
from app.services.match_service import MatchService
from app.services.standing_service import StandingService

client = TestClient(app)


class FakeAllowedLeagueRepository:
    def __init__(self, existing=None):
        self.existing = existing
        self.created = []
        self.deleted = []

    async def get_all(self, db):
        return list(self.existing or [])

    async def get_by_league_id(self, db, league_id):
        for item in self.existing or []:
            if getattr(item, "league_id", None) == league_id:
                return item
        return None

    async def create(self, db, league_id):
        self.created.append(league_id)
        return type("AllowedLeague", (), {"league_id": league_id})()

    async def delete(self, db, allowed_league):
        self.deleted.append(getattr(allowed_league, "league_id", None))


class FakeLeagueQueryResult:
    def __init__(self, league):
        self._league = league

    def scalar_one_or_none(self):
        return self._league


class FakeDB:
    def __init__(self, league=None):
        self.league = league
        self.committed = False
        self.rolled_back = False

    async def execute(self, query):
        return FakeLeagueQueryResult(self.league)

    async def flush(self):
        return None

    async def rollback(self):
        self.rolled_back = True


class FakeCacheService:
    def delete_sync(self, key):
        return None


def test_admin_allowed_leagues_route_exists():
    assert "get_allowed_leagues" in [route.name for route in admin_leagues_router.routes]
    assert "create_allowed_league" in [route.name for route in admin_leagues_router.routes]
    assert "delete_allowed_league" in [route.name for route in admin_leagues_router.routes]


def test_admin_allowed_leagues_route_is_in_openapi():
    response = client.get("/api/openapi.json")

    assert response.status_code == 200
    assert "/api/admin/allowed-leagues" in response.json()["paths"]


def test_allowed_league_service_rejects_invalid_league_id():
    service = AllowedLeagueService()

    async def run():
        with pytest.raises(ValueError):
            await service.add_allowed_league(FakeDB(), 0)

    asyncio.run(run())


def test_allowed_league_service_prevents_duplicates():
    service = AllowedLeagueService(repository=FakeAllowedLeagueRepository(existing=[type("AllowedLeague", (), {"league_id": 39})()]))

    async def run():
        with pytest.raises(HTTPException) as exc:
            await service.add_allowed_league(FakeDB(league=object()), 39)

    asyncio.run(run())


def test_allowed_league_service_removes_allowed_league():
    repo = FakeAllowedLeagueRepository(existing=[type("AllowedLeague", (), {"league_id": 39})()])
    service = AllowedLeagueService(repository=repo)

    async def run():
        await service.remove_allowed_league(FakeDB(), 39)

    asyncio.run(run())

    assert repo.deleted == [39]


class FakeRepoWithIntegrityError:
    async def get_by_league_id(self, db, league_id):
        return None

    async def create(self, db, league_id):
        raise IntegrityError("duplicate", None, None)


class FakeCommitDB:
    def __init__(self):
        self.added = []
        self.committed = False
        self.rolled_back = False
        self.refreshed = False

    async def flush(self):
        return None

    async def refresh(self, instance):
        self.refreshed = True

    async def commit(self):
        self.committed = True

    async def rollback(self):
        self.rolled_back = True

    def add(self, instance):
        self.added.append(instance)


class FakeFailingDB(FakeCommitDB):
    async def flush(self):
        raise RuntimeError("flush failed")


class FakeDeleteDB(FakeCommitDB):
    async def delete(self, instance):
        self.deleted = instance


class FakeDeleteFailingDB(FakeFailingDB):
    async def delete(self, instance):
        self.deleted = instance


def test_allowed_league_repository_create_commits_and_refreshes():
    db = FakeCommitDB()
    repo = AllowedLeagueRepository()

    async def run():
        result = await repo.create(db, 39)
        assert result.league_id == 39
        assert db.added and db.added[0].league_id == 39
        assert db.committed is True
        assert db.refreshed is True

    asyncio.run(run())


def test_allowed_league_repository_delete_commits():
    db = FakeDeleteDB()
    repo = AllowedLeagueRepository()
    item = type("AllowedLeague", (), {"league_id": 39})()

    async def run():
        await repo.delete(db, item)

    asyncio.run(run())

    assert db.deleted is item
    assert db.committed is True


def test_allowed_league_service_converts_duplicate_integrity_error_to_conflict():
    service = AllowedLeagueService(repository=FakeRepoWithIntegrityError())
    exc_info = None

    async def run():
        nonlocal exc_info
        with pytest.raises(HTTPException) as captured:
            await service.add_allowed_league(FakeDB(league=object()), 39)
        exc_info = captured

    asyncio.run(run())

    assert exc_info is not None
    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "League is already allowed"


def test_allowed_league_repository_rolls_back_on_failure():
    db = FakeFailingDB()
    repo = AllowedLeagueRepository()

    async def run():
        with pytest.raises(RuntimeError):
            await repo.create(db, 39)

    asyncio.run(run())

    assert db.rolled_back is True
    assert db.committed is False


class FakeAllowedIdsRepository:
    def __init__(self, allowed_ids):
        self.allowed_ids = set(allowed_ids)

    async def get_allowed_ids(self, db):
        return set(self.allowed_ids)


class FakeMatchClient:
    async def get(self, path, params=None):
        return {"response": [
            {"fixture": {"id": 1001, "date": "2026-06-15T18:00:00+00:00", "status": {"short": "NS", "elapsed": 0}, "venue": {"name": "A", "city": "B"}},
             "league": {"id": 39, "name": "Allowed League", "country": "X", "logo": None, "flag": None},
             "teams": {"home": {"id": 1, "name": "Home", "logo": None}, "away": {"id": 2, "name": "Away", "logo": None}},
             "goals": {"home": 0, "away": 0},
            },
            {"fixture": {"id": 1002, "date": "2026-06-15T18:00:00+00:00", "status": {"short": "NS", "elapsed": 0}, "venue": {"name": "A", "city": "B"}},
             "league": {"id": 999, "name": "Blocked League", "country": "Y", "logo": None, "flag": None},
             "teams": {"home": {"id": 3, "name": "Home2", "logo": None}, "away": {"id": 4, "name": "Away2", "logo": None}},
             "goals": {"home": 0, "away": 0},
            },
        ]}


class RecordingMatchService(MatchService):
    def __init__(self, client, allowed_ids):
        super().__init__(client=client, team_service=object(), cache_service=FakeCacheService())
        self.allowed_league_repository = FakeAllowedIdsRepository(allowed_ids)
        self.seen_fixtures = None

    async def _process_sync(self, db, fixtures):
        allowed_ids = await self.allowed_league_repository.get_allowed_ids(db)
        filtered = [fixture for fixture in fixtures if int((fixture.get("league") or {}).get("id", 0)) in allowed_ids]
        self.seen_fixtures = filtered
        return {"success": True, "inserted": len(filtered), "updated": 0, "total": len(filtered)}


class FakeStandingClient:
    async def get(self, path, params=None):
        return {"response": []}


class FakeLeagueSyncClient:
    async def get(self, path, params=None):
        return {"response": [
            {"league": {"id": 39, "name": "Allowed League", "country": "X", "logo": None}},
            {"league": {"id": 999, "name": "Blocked League", "country": "Y", "logo": None}},
        ]}


class RecordingLeagueService(LeagueService):
    def __init__(self, client, allowed_ids):
        super().__init__(client=client, cache_service=FakeCacheService())
        self.allowed_league_repository = FakeAllowedIdsRepository(allowed_ids)
        self.upserted = []

    async def upsert_league(self, db, league_data, allowed_ids=None):
        self.upserted.append(league_data)
        return league_data


class RecordingStandingService(StandingService):
    def __init__(self, client, allowed_ids):
        super().__init__(client=client, team_service=object(), cache_service=FakeCacheService())
        self.allowed_league_repository = FakeAllowedIdsRepository(allowed_ids)


class FakeSyncDB:
    async def execute(self, query):
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

    async def refresh(self, obj):
        return None

    def add(self, obj):
        return None


class FakeCacheService:
    def __init__(self):
        self.deleted = []

    def delete_sync(self, key):
        self.deleted.append(key)


class FakeTeamService:
    async def ensure_teams_exist(self, db, teams):
        return None


class CountingAllowedLeagueRepository:
    def __init__(self, allowed_ids):
        self.allowed_ids = set(allowed_ids)
        self.calls = 0

    async def get_allowed_ids(self, db):
        self.calls += 1
        return set(self.allowed_ids)


class CountingLeagueRepository:
    async def get_by_id(self, db, league_id):
        return None

    async def get_many_by_ids(self, db, league_ids):
        return []


class CountingDB:
    async def execute(self, query):
        return type("Result", (), {"scalar_one_or_none": lambda self: None})()

    async def flush(self):
        return None

    async def refresh(self, obj):
        return None

    def add(self, obj):
        return None


def test_league_sync_uses_allowed_ids_only_once_per_run():
    service = LeagueService(client=FakeLeagueSyncClient(), cache_service=FakeCacheService())
    repository = CountingAllowedLeagueRepository([39])
    service.allowed_league_repository = repository
    service.league_repository = CountingLeagueRepository()

    async def run():
        return await service.sync_all_leagues(CountingDB())

    result = asyncio.run(run())

    assert result["success"] is True
    assert repository.calls == 1


def test_match_service_sync_full_season_filters_unallowed_leagues_before_write():
    service = RecordingMatchService(FakeMatchClient(), [39])

    async def run():
        result = await service.sync_full_season(FakeSyncDB(), 39, 2024)
        return result

    result = asyncio.run(run())

    assert result["inserted"] == 1
    assert len(service.seen_fixtures) == 1
    assert service.seen_fixtures[0]["league"]["id"] == 39


def test_match_service_sync_daily_fixtures_filters_unallowed_leagues_before_write():
    service = RecordingMatchService(FakeMatchClient(), [39])

    async def run():
        result = await service.sync_daily_fixtures(FakeSyncDB(), "2026-06-15")
        return result

    result = asyncio.run(run())

    assert result["inserted"] == 1
    assert len(service.seen_fixtures) == 1
    assert service.seen_fixtures[0]["league"]["id"] == 39


def test_league_service_sync_all_leagues_skips_unallowed_leagues():
    service = RecordingLeagueService(FakeLeagueSyncClient(), [39])

    async def run():
        result = await service.sync_all_leagues(FakeSyncDB())
        return result

    result = asyncio.run(run())

    assert result["success"] is True
    assert result["inserted"] == 1
    assert result["updated"] == 0
    assert len(service.upserted) == 1
    assert service.upserted[0]["league"]["id"] == 39


def test_standing_service_sync_standings_skips_unallowed_league():
    service = RecordingStandingService(FakeStandingClient(), [])

    async def run():
        result = await service.sync_standings(FakeSyncDB(), 999, 2024)
        return result

    result = asyncio.run(run())

    assert result["success"] is True
    assert result["updated"] == 0
    assert result["message"] == "League is not allowed for synchronization"


def test_standing_service_flatten_standings_groups_single_group():
    service = StandingService(client=FakeStandingClient(), team_service=FakeTeamService(), cache_service=FakeCacheService())
    api_result = {
        "response": [
            {
                "league": {
                    "standings": [
                        [
                            {
                                "rank": 1,
                                "team": {"id": 1, "name": "One", "logo": None, "country": "X"},
                                "points": 10,
                                "all": {"played": 5, "win": 3, "draw": 1, "lose": 1},
                                "goalsDiff": 5,
                            }
                        ]
                    ]
                }
            }
        ]
    }

    flattened = service._flatten_standings_groups(api_result)
    assert isinstance(flattened, list)
    assert len(flattened) == 1
    assert flattened[0]["team"]["id"] == 1


def test_standing_service_flatten_standings_groups_multi_group():
    service = StandingService(client=FakeStandingClient(), team_service=FakeTeamService(), cache_service=FakeCacheService())
    api_result = {
        "response": [
            {
                "league": {
                    "standings": [
                        [
                            {"rank": 1, "team": {"id": 1, "name": "One", "logo": None, "country": "X"}, "points": 10, "all": {"played": 5, "win": 3, "draw": 1, "lose": 1}, "goalsDiff": 5},
                            {"rank": 2, "team": {"id": 2, "name": "Two", "logo": None, "country": "Y"}, "points": 8, "all": {"played": 5, "win": 2, "draw": 2, "lose": 1}, "goalsDiff": 1},
                        ],
                        [
                            {"rank": 1, "team": {"id": 3, "name": "Three", "logo": None, "country": "Z"}, "points": 12, "all": {"played": 5, "win": 4, "draw": 0, "lose": 1}, "goalsDiff": 8},
                            {"rank": 2, "team": {"id": 4, "name": "Four", "logo": None, "country": "W"}, "points": 9, "all": {"played": 5, "win": 3, "draw": 0, "lose": 2}, "goalsDiff": 4},
                        ],
                    ]
                }
            }
        ]
    }

    flattened = service._flatten_standings_groups(api_result)
    assert isinstance(flattened, list)
    assert len(flattened) == 4
    assert {team["team"]["id"] for team in flattened} == {1, 2, 3, 4}


def test_standing_service_sync_standings_multi_group_flattened_rows():
    class FakeMultiGroupStandingClient:
        async def get(self, path, params=None):
            return {
                "response": [
                    {
                        "league": {
                            "standings": [
                                [
                                    {"rank": 1, "team": {"id": 1, "name": "One", "logo": None, "country": "X"}, "points": 10, "all": {"played": 5, "win": 3, "draw": 1, "lose": 1}, "goalsDiff": 5},
                                ],
                                [
                                    {"rank": 1, "team": {"id": 2, "name": "Two", "logo": None, "country": "Y"}, "points": 12, "all": {"played": 5, "win": 4, "draw": 0, "lose": 1}, "goalsDiff": 8},
                                ],
                            ]
                        }
                    }
                ]
            }

    class RecordingMultiGroupStandingService(StandingService):
        async def upsert_standings(self, db, standings_data, league_id, season):
            self.recorded_standings = standings_data
            await super().upsert_standings(db, standings_data, league_id, season)

    service = RecordingMultiGroupStandingService(
        client=FakeMultiGroupStandingClient(),
        team_service=FakeTeamService(),
        cache_service=FakeCacheService(),
    )
    service.allowed_league_repository = FakeAllowedIdsRepository([1])

    async def run():
        return await service.sync_standings(FakeSyncDB(), 1, 2026)

    result = asyncio.run(run())
    assert result["success"] is True
    assert result["updated"] == 2
    assert hasattr(service, "recorded_standings")
    assert len(service.recorded_standings) == 2
    assert {row["team"]["id"] for row in service.recorded_standings} == {1, 2}


def test_allowed_leagues_admin_requires_admin_access():
    async def override_admin():
        raise HTTPException(status_code=403, detail="Forbidden")

    app.dependency_overrides[get_current_active_admin] = override_admin
    try:
        response = client.get("/api/admin/allowed-leagues")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 403


def test_allowed_league_migration_exists_and_contains_expected_operations():
    migration_files = list(Path("alembic/versions").glob("*.py"))
    assert migration_files, "No Alembic migrations found"

    migration_text = "\n".join(path.read_text(encoding="utf-8") for path in migration_files)

    assert "allowed_leagues" in migration_text
    assert "op.create_table(" in migration_text
    assert "op.drop_table(" in migration_text


def test_allowed_league_migration_supports_downgrade():
    migration_files = list(Path("alembic/versions").glob("*.py"))
    assert migration_files, "No Alembic migrations found"

    migration_text = "\n".join(path.read_text(encoding="utf-8") for path in migration_files)

    assert "def downgrade" in migration_text
    assert "allowed_leagues" in migration_text
