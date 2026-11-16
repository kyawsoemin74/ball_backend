import asyncio
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.dialects import postgresql

from app.api import admin_player_sync
from app.api.deps import current_active_admin
from app.core.security import get_current_user
from app.db import get_db
from app.repositories.player_repository import PlayerRepository
from app.services.player_master_rebuild_service import PlayerMasterRebuildService
from app.services.player_sync_service import (
    PlayerIdentityResolutionError,
    PlayerSyncService,
)


class FakeResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class FakePlayerRepository:
    def __init__(self, existing=None, outcome="updated"):
        self.existing = existing
        self.outcome = outcome
        self.upserts = []

    async def get_by_provider_id(self, _db, provider_id, provider):
        if self.existing is not None:
            assert (provider, provider_id) == (
                self.existing.provider,
                self.existing.provider_id,
            )
        return self.existing

    async def upsert_squad_photo(self, db, **values):
        self.upserts.append((db, values))
        return self.existing or SimpleNamespace(
            provider=values["provider"],
            provider_id=values["provider_id"],
            name=values["name"],
            photo=values["photo"],
        ), self.outcome


class FakeIdentityResolver:
    def __init__(self, result):
        self.result = result

    async def resolve_provider_player_identity(self, *_args, **_kwargs):
        return self.result


def _resolved_existing(player_id=12, evidence="provider+provider_id exact match"):
    return SimpleNamespace(
        status="RESOLVED_EXISTING",
        provider="api-football",
        provider_id="56080",
        local_player_id=player_id,
        evidence=evidence,
        reason=None,
    )


def _player_sync(repository, resolution):
    return PlayerSyncService(
        player_provider=SimpleNamespace(),
        player_repository=repository,
        player_identity_resolution_service=FakeIdentityResolver(resolution),
    )


def test_squad_photo_updates_exact_player_without_profile_fields():
    existing = SimpleNamespace(
        player_id=12,
        provider="api-football",
        provider_id="56080",
        name="N. Alip",
        photo=None,
    )
    repository = FakePlayerRepository(existing)
    service = _player_sync(repository, _resolved_existing())

    result = asyncio.run(
        service.sync_squad_player_photo(
            object(),
            {
                "id": 56080,
                "name": "N. Alip",
                "photo": " https://media.api-sports.io/football/players/56080.png ",
            },
        )
    )

    assert result["status"] == "updated"
    assert repository.upserts[0][1] == {
        "provider": "api-football",
        "provider_id": "56080",
        "name": "N. Alip",
        "photo": "https://media.api-sports.io/football/players/56080.png",
    }


def test_squad_photo_creates_new_player_only_when_photo_is_valid():
    repository = FakePlayerRepository(outcome="inserted")
    resolution = SimpleNamespace(
        status="CREATE_NEW",
        provider="api-football",
        provider_id="56080",
        local_player_id=None,
        evidence="no existing identity",
        reason=None,
    )
    service = _player_sync(repository, resolution)

    result = asyncio.run(
        service.sync_squad_player_photo(
            object(),
            {"id": 56080, "name": "N. Alip", "photo": "https://example.test/56080.png"},
        )
    )

    assert result["status"] == "inserted"
    assert repository.upserts[0][1]["name"] == "N. Alip"
    assert repository.upserts[0][1]["photo"] == "https://example.test/56080.png"


def test_new_player_without_valid_photo_is_not_inserted():
    repository = FakePlayerRepository()
    resolution = SimpleNamespace(
        status="CREATE_NEW",
        provider="api-football",
        provider_id="56080",
        local_player_id=None,
        evidence="no existing identity",
        reason=None,
    )
    service = _player_sync(repository, resolution)

    result = asyncio.run(
        service.sync_squad_player_photo(
            object(),
            {"id": 56080, "name": "N. Alip", "photo": None},
        )
    )

    assert result == {"status": "skipped", "reason": "missing_photo"}
    assert repository.upserts == []


@pytest.mark.parametrize(
    ("photo", "reason"),
    [(None, "missing_photo"), ("   ", "invalid_photo"), ("javascript:bad", "invalid_photo")],
)
def test_missing_or_invalid_squad_photo_preserves_existing_valid_photo(photo, reason):
    existing = SimpleNamespace(
        player_id=12,
        provider="api-football",
        provider_id="56080",
        name="N. Alip",
        photo="https://example.test/existing.png",
    )
    repository = FakePlayerRepository(existing)
    service = _player_sync(repository, _resolved_existing())

    result = asyncio.run(
        service.sync_squad_player_photo(
            object(),
            {"id": 56080, "name": "N. Alip", "photo": photo},
        )
    )

    assert result == {"status": "preserved", "reason": reason}
    assert repository.upserts == []


@pytest.mark.parametrize(
    "resolution",
    [
        SimpleNamespace(status="AMBIGUOUS", evidence=None, reason="multiple candidates"),
        SimpleNamespace(
            status="RESOLVED_EXISTING",
            local_player_id=12,
            evidence="authoritative local name/nationality evidence matched a single empty-provider candidate",
            reason="legacy candidate",
        ),
    ],
)
def test_squad_photo_rejects_ambiguous_and_legacy_name_identity(resolution):
    repository = FakePlayerRepository()
    service = _player_sync(repository, resolution)

    with pytest.raises(PlayerIdentityResolutionError):
        asyncio.run(
            service.sync_squad_player_photo(
                object(),
                {"id": 56080, "name": "N. Alip", "photo": "https://example.test/p.png"},
            )
        )

    assert repository.upserts == []


def test_repository_photo_upsert_changes_only_photo_for_existing_player():
    player = SimpleNamespace(player_id=12)

    class FakeDB:
        def __init__(self):
            self.statements = []
            self.results = [FakeResult(None), FakeResult(12), FakeResult(player)]

        async def execute(self, statement):
            self.statements.append(statement)
            return self.results.pop(0)

    db = FakeDB()
    result_player, outcome = asyncio.run(
        PlayerRepository().upsert_squad_photo(
            db,
            provider="api-football",
            provider_id="56080",
            name="N. Alip",
            photo="https://example.test/p.png",
        )
    )

    update_sql = str(db.statements[1].compile(dialect=postgresql.dialect()))
    assert result_player is player
    assert outcome == "updated"
    assert "SET photo=" in update_sql
    assert "updated_at=now()" in update_sql
    assert "name=" not in update_sql
    assert len(db.statements) == 3


def test_rebuild_service_scopes_photo_sync_to_one_local_team():
    calls = []

    class FakeTeamRepository:
        async def get_by_id(self, _db, team_id):
            assert team_id == 7
            return SimpleNamespace(provider="api-football", provider_id="123")

    class FakePlayerProvider:
        async def collect_team_squad(self, provider_team_id):
            calls.append(provider_team_id)
            return {
                "provider_request_status": "SUCCESS",
                "players": [
                    {"id": 1, "name": "Player One", "photo": "https://example.test/1.png"},
                    {"id": 2, "name": "Player Two", "photo": None},
                ],
            }

    class FakePlayerSync:
        player_repository = object()

        async def sync_squad_player_photo(self, _db, player):
            return {
                "status": "inserted" if player["id"] == 1 else "skipped",
                "reason": "missing_photo" if player["id"] == 2 else "",
            }

    service = PlayerMasterRebuildService(
        league_provider=object(),
        team_provider=object(),
        player_provider=FakePlayerProvider(),
        team_repository=FakeTeamRepository(),
        team_sync_service=object(),
        player_sync_service=FakePlayerSync(),
        membership_service=object(),
    )
    result = asyncio.run(service.sync_team_squad_photos(object(), 7))

    assert calls == [123]
    assert result == {
        "success": True,
        "team_id": 7,
        "provider_team_id": "123",
        "players_collected": 2,
        "photos_inserted": 1,
        "photos_updated": 0,
        "existing_photos_preserved": 0,
        "players_skipped": 1,
        "invalid_photos": 0,
        "missing_photos": 1,
    }


def test_rebuild_service_rejects_duplicate_provider_player_ids_before_writes():
    class FakeTeamRepository:
        async def get_by_id(self, _db, _team_id):
            return SimpleNamespace(provider="api-football", provider_id="123")

    class FakePlayerProvider:
        async def collect_team_squad(self, _provider_team_id):
            return {
                "provider_request_status": "SUCCESS",
                "players": [
                    {"id": 56080, "name": "Player One", "photo": "https://example.test/1.png"},
                    {"id": "56080", "name": "Different record", "photo": "https://example.test/2.png"},
                ],
            }

    class FakePlayerSync:
        player_repository = object()

        def __init__(self):
            self.calls = []

        async def sync_squad_player_photo(self, _db, player):
            self.calls.append(player)
            return {"status": "inserted", "reason": ""}

    player_sync = FakePlayerSync()
    service = PlayerMasterRebuildService(
        league_provider=object(),
        team_provider=object(),
        player_provider=FakePlayerProvider(),
        team_repository=FakeTeamRepository(),
        team_sync_service=object(),
        player_sync_service=player_sync,
        membership_service=object(),
    )

    with pytest.raises(PlayerIdentityResolutionError):
        asyncio.run(service.sync_team_squad_photos(object(), 7))

    assert player_sync.calls == []


class FakeHTTPDB:
    def __init__(self, *, commit_error=None):
        self.events = []
        self.commit_error = commit_error

    async def commit(self):
        if self.commit_error is not None:
            raise self.commit_error
        self.events.append("commit")

    async def rollback(self):
        self.events.append("rollback")


def _admin_test_app(monkeypatch, db, service_result, *, lock_acquired=True):
    test_app = FastAPI()
    test_app.include_router(admin_player_sync.router, prefix="/api/admin")
    calls = []

    class FakeService:
        async def sync_team_squad_photos(self, current_db, team_id):
            assert current_db is db
            calls.append(team_id)
            if isinstance(service_result, Exception):
                raise service_result
            return service_result

    async def override_db():
        yield db

    async def override_admin():
        return SimpleNamespace(role="admin", is_active=True)

    async def fake_lock(_db, resource_type, resource_identity, operation):
        assert (resource_type, resource_identity) == ("player_squad_photo", 7)
        if not lock_acquired:
            return False, None
        return True, await operation()

    test_app.dependency_overrides[get_db] = override_db
    test_app.dependency_overrides[current_active_admin] = override_admin
    monkeypatch.setattr(
        admin_player_sync,
        "build_player_master_rebuild_service",
        lambda: FakeService(),
    )
    monkeypatch.setattr(admin_player_sync, "run_with_resource_lock", fake_lock)
    return test_app, calls


def test_admin_squad_photo_sync_commits_only_after_service_success(monkeypatch):
    db = FakeHTTPDB()
    result = {
        "success": True,
        "team_id": 7,
        "provider_team_id": "123",
        "players_collected": 1,
        "photos_inserted": 1,
        "photos_updated": 0,
        "existing_photos_preserved": 0,
        "players_skipped": 0,
        "invalid_photos": 0,
        "missing_photos": 0,
    }
    test_app, calls = _admin_test_app(monkeypatch, db, result)

    response = TestClient(test_app).post(
        "/api/admin/players/squad-photos/sync/7"
    )

    assert response.status_code == 200
    assert response.json() == result
    assert calls == [7]
    assert db.events == ["commit"]


def test_admin_squad_photo_sync_rolls_back_provider_failure(monkeypatch):
    db = FakeHTTPDB()
    test_app, _ = _admin_test_app(
        monkeypatch,
        db,
        ConnectionError("provider unavailable"),
    )

    response = TestClient(test_app).post(
        "/api/admin/players/squad-photos/sync/7"
    )

    assert response.status_code == 503
    assert db.events == ["rollback"]


def test_admin_squad_photo_sync_rolls_back_identity_failure(monkeypatch):
    db = FakeHTTPDB()
    test_app, _ = _admin_test_app(
        monkeypatch,
        db,
        PlayerIdentityResolutionError("identity conflict"),
    )

    response = TestClient(test_app).post(
        "/api/admin/players/squad-photos/sync/7"
    )

    assert response.status_code == 409
    assert db.events == ["rollback"]


def test_admin_squad_photo_sync_rolls_back_failed_commit(monkeypatch):
    db = FakeHTTPDB(commit_error=RuntimeError("commit failed"))
    test_app, _ = _admin_test_app(
        monkeypatch,
        db,
        {
            "success": True,
            "team_id": 7,
            "provider_team_id": "123",
            "players_collected": 0,
            "photos_inserted": 0,
            "photos_updated": 0,
            "existing_photos_preserved": 0,
            "players_skipped": 0,
            "invalid_photos": 0,
            "missing_photos": 0,
        },
    )

    response = TestClient(test_app).post(
        "/api/admin/players/squad-photos/sync/7"
    )

    assert response.status_code == 500
    assert db.events == ["rollback"]


def test_admin_squad_photo_sync_conflict_rolls_back_without_sync(monkeypatch):
    db = FakeHTTPDB()
    test_app, calls = _admin_test_app(
        monkeypatch,
        db,
        {"success": True},
        lock_acquired=False,
    )

    response = TestClient(test_app).post(
        "/api/admin/players/squad-photos/sync/7"
    )

    assert response.status_code == 409
    assert calls == []
    assert db.events == ["rollback"]


def test_admin_squad_photo_sync_requires_admin_and_positive_team_id():
    test_app = FastAPI()
    test_app.include_router(admin_player_sync.router, prefix="/api/admin")
    client = TestClient(test_app)

    assert client.post("/api/admin/players/squad-photos/sync/7").status_code == 401

    async def normal_user():
        return SimpleNamespace(role="user", is_active=True)

    test_app.dependency_overrides[get_current_user] = normal_user
    assert client.post("/api/admin/players/squad-photos/sync/7").status_code == 403

    async def admin_user():
        return SimpleNamespace(role="admin", is_active=True)

    test_app.dependency_overrides[get_current_user] = admin_user
    assert client.post("/api/admin/players/squad-photos/sync/0").status_code == 422
