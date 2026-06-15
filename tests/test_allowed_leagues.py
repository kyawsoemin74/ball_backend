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
