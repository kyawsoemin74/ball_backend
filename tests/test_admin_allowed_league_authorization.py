from types import SimpleNamespace

from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.api import admin_leagues
from app.core.security import get_current_active_admin
from app.db import get_db
from app.main import app


client = TestClient(app)


class FakeDB:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


class FakeCache:
    def __init__(self, events):
        self.events = events

    async def delete(self, key):
        self.events.append(("cache", key))


def test_authorization_uses_local_league_id_and_invalidates_after_commit(monkeypatch):
    db = FakeDB()
    events = []

    class FakeService:
        async def add_allowed_league(self, current_db, league_id):
            assert current_db is db
            assert league_id == 1
            return SimpleNamespace(league_id=1), True

    monkeypatch.setattr(admin_leagues, "AllowedLeagueService", lambda: FakeService())
    monkeypatch.setattr(admin_leagues, "CacheService", lambda: FakeCache(events))

    async def override_db():
        yield db

    async def override_admin():
        return SimpleNamespace(id=7, username="admin", role="admin", is_active=True)

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_active_admin] = override_admin
    try:
        response = client.post("/api/admin/allowed-leagues", json={"league_id": 1})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {"league_id": 1}
    assert db.commits == 1
    assert events == [("cache", "fover:leagues_grouped")]


def test_authorization_existing_row_is_idempotent(monkeypatch):
    db = FakeDB()
    cache_events = []

    class FakeService:
        async def add_allowed_league(self, current_db, league_id):
            return SimpleNamespace(league_id=1), False

    monkeypatch.setattr(admin_leagues, "AllowedLeagueService", lambda: FakeService())
    monkeypatch.setattr(admin_leagues, "CacheService", lambda: FakeCache(cache_events))

    async def override_db():
        yield db

    async def override_admin():
        return SimpleNamespace(role="admin", is_active=True)

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_active_admin] = override_admin
    try:
        response = client.post("/api/admin/allowed-leagues", json={"league_id": 1})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {"league_id": 1}
    assert db.commits == 1
    assert cache_events == []


def test_authorization_rejects_non_admin():
    async def override_admin():
        raise HTTPException(status_code=403, detail="Forbidden")

    app.dependency_overrides[get_current_active_admin] = override_admin
    try:
        response = client.post("/api/admin/allowed-leagues", json={"league_id": 1})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 403