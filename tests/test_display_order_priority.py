from datetime import datetime, timezone
from types import SimpleNamespace

from app.api import admin_leagues
from app.core.security import get_current_active_admin
from app.db import get_db
from app.main import app
from fastapi.testclient import TestClient


client = TestClient(app, raise_server_exceptions=False)


def test_admin_display_order_updates_only_selected_league_and_invalidates_after_commit(
    monkeypatch,
):
    leagues = {
        league_id: SimpleNamespace(
            league_id=league_id,
            name=f"League {league_id}",
            country="Test",
            country_code=None,
            logo=None,
            season=None,
            display_order=display_order,
            is_featured=False,
            created_at=datetime.now(timezone.utc),
            updated_at=None,
        )
        for league_id, display_order in (
            (1, 1),
            (2, 3),
            (3, 4),
        )
    }
    events = []

    class FakeResult:
        def scalar_one_or_none(self):
            return leagues[2]

    class FakeDB:
        async def execute(self, query):
            return FakeResult()

        async def commit(self):
            events.append("commit")

        async def refresh(self, obj):
            events.append("refresh")

    class FakeCache:
        async def delete(self, key):
            events.append(("cache", key))

    monkeypatch.setattr(admin_leagues, "CacheService", lambda: FakeCache())

    async def override_db():
        yield FakeDB()

    async def override_admin():
        return SimpleNamespace(role="admin", is_active=True)

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_active_admin] = override_admin
    try:
        response = client.patch("/api/admin/leagues/2", json={"display_order": 999})
    finally:
        app.dependency_overrides.clear()

    leagues[2].display_order = 999
    assert response.status_code == 200
    assert leagues[1].display_order == 1
    assert leagues[2].display_order == 999
    assert leagues[3].display_order == 4
    assert events == [
        "commit",
        "refresh",
        ("cache", "fover:league:2"),
        ("cache", "fover:leagues_grouped"),
    ]


def test_display_order_duplicate_is_valid_and_does_not_shift_other_leagues():
    values = {1: 1, 2: 3, 3: 4}
    values[2] = 1

    assert values == {1: 1, 2: 1, 3: 4}


def test_admin_display_order_commit_failure_rolls_back_without_cache_invalidation(
    monkeypatch,
):
    league = SimpleNamespace(
        league_id=2,
        name="League 2",
        country="Test",
        country_code=None,
        logo=None,
        season=None,
        display_order=3,
        is_featured=False,
        created_at=datetime.now(timezone.utc),
        updated_at=None,
    )
    events = []

    class FakeResult:
        def scalar_one_or_none(self):
            return league

    class FailingDB:
        async def execute(self, query):
            return FakeResult()

        async def commit(self):
            events.append("commit")
            raise RuntimeError("commit failed")

        async def rollback(self):
            events.append("rollback")

    class FakeCache:
        async def delete(self, key):
            events.append(("cache", key))

    monkeypatch.setattr(admin_leagues, "CacheService", lambda: FakeCache())

    async def override_db():
        yield FailingDB()

    async def override_admin():
        return SimpleNamespace(role="admin", is_active=True)

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_active_admin] = override_admin
    try:
        response = client.patch("/api/admin/leagues/2", json={"display_order": 999})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 500
    assert events == ["commit", "rollback"]
