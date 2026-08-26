import asyncio

import pytest

from app.api import matches as matches_api
from app.core.security import get_current_active_admin
from app.api.matches import refresh_h2h_route
from app.services.h2h_service import H2HService
from app.services.h2h_sync_service import H2HSyncService


class Team:
    def __init__(self, team_id):
        self.team_id = team_id


class Provider:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def get_h2h_by_key(self, key):
        self.calls.append(key)
        return self.response


class SourceRepository:
    def __init__(self):
        self.keys = []
        self.rows = {}

    async def upsert_one(self, db, key, data):
        self.keys.append(key)
        self.rows[key] = data


class TeamRepository:
    async def find_by_provider_identity(self, db, provider, provider_id):
        return {1: Team(20), 2: Team(10)}.get(int(provider_id))


class Projection:
    def __init__(self):
        self.calls = []

    async def project_h2h(self, db, low, high, fixtures):
        self.calls.append((low, high, fixtures))
        return {
            "source_count": len(fixtures),
            "accepted_count": len(fixtures),
            "rejected_count": 0,
            "duplicate_count": 0,
            "unresolved_count": 0,
            "orphan_count": 0,
            "analytics_count": len(fixtures),
            "success": True,
        }


class DB:
    def __init__(self):
        self.flushes = 0
        self.commits = 0
        self.rollbacks = 0

    async def flush(self):
        self.flushes += 1

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


def make_sync(response):
    provider = Provider(response)
    source = SourceRepository()
    projection = Projection()
    sync = H2HSyncService(
        h2h_service=None,
        h2h_provider=provider,
        h2h_repository=source,
        team_repository=TeamRepository(),
        analytics_projection_service=projection,
    )
    return sync, provider, source, projection


def test_explicit_refresh_writes_canonical_local_source_key_and_projects():
    sync, provider, source, projection = make_sync({"response": []})
    result = asyncio.run(sync.refresh_h2h(DB(), "1-2"))

    assert result["updated"] is True
    assert provider.calls == ["1-2"]
    assert source.keys == ["10-20"]
    assert projection.calls == [(1, 2, [])]


def test_provider_failure_does_not_write_source_or_analytics():
    sync, provider, source, projection = make_sync(None)
    result = asyncio.run(sync.refresh_h2h(DB(), "1-2"))

    assert result == {"error": "API error"}
    assert source.keys == []
    assert projection.calls == []


def test_invalid_provider_response_fails_before_source_write():
    sync, provider, source, projection = make_sync({"response": {"unexpected": True}})

    with pytest.raises(ValueError, match="Invalid H2H"):
        asyncio.run(sync.refresh_h2h(DB(), "1-2"))

    assert source.keys == []
    assert projection.calls == []


class Result:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class ReadDB:
    async def execute(self, query):
        query_text = str(query)
        if "match_h2h" in query_text:
            return Result(None)
        if "matches" in query_text:
            return Result(object())
        return Result(Team(1))


class Cache:
    async def get_json(self, key):
        return None

    async def set_json(self, *args):
        raise AssertionError("GET must not refresh or publish H2H")


def test_get_cached_h2h_does_not_invoke_explicit_refresh(monkeypatch):
    service = H2HService(client=object(), cache_service=Cache())

    async def fail_refresh(*args, **kwargs):
        raise AssertionError("GET invoked H2H refresh")

    monkeypatch.setattr(service.h2h_sync_service, "refresh_h2h", fail_refresh)
    result = asyncio.run(service.get_cached_h2h(ReadDB(), 1, 2, 99))

    assert result is None


def test_operational_h2h_trigger_is_admin_post_and_sanitizes_provider_payload(monkeypatch):
    async def refresh_h2h(**kwargs):
        return {
            "updated": True,
            "data": [{"secret_provider_field": "must_not_return"}],
            "analytics": {
                "fact": "h2h",
                "scope": {"team_low_id": 10, "team_high_id": 20},
                "source_count": 1,
                "accepted_count": 1,
                "rejected_count": 0,
                "duplicate_count": 0,
                "unresolved_count": 0,
                "orphan_count": 0,
                "analytics_count": 1,
                "success": True,
            },
        }

    monkeypatch.setattr(matches_api.football_service, "refresh_h2h", refresh_h2h)
    db = DB()
    result = asyncio.run(refresh_h2h_route(20, 10, db))

    assert result["success"] is True
    assert "data" not in result
    assert result["analytics"]["transaction_outcome"] == "committed"
    assert db.commits == 1
    assert db.rollbacks == 0
    route = next(item for item in matches_api.router.routes if item.path == "/matches/sync/h2h/{team1_id}/{team2_id}")
    assert "POST" in route.methods
    assert route.dependencies


def test_operational_h2h_trigger_rejects_same_team_before_service(monkeypatch):
    async def fail_refresh(**kwargs):
        raise AssertionError("service should not be called")

    monkeypatch.setattr(matches_api.football_service, "refresh_h2h", fail_refresh)
    with pytest.raises(Exception) as raised:
        asyncio.run(refresh_h2h_route(7, 7, DB()))
    assert getattr(raised.value, "status_code", None) == 422


def test_operational_h2h_trigger_requires_existing_admin_dependency():
    route = next(item for item in matches_api.router.routes if item.path == "/matches/sync/h2h/{team1_id}/{team2_id}")
    assert route.dependencies[0].dependency is get_current_active_admin


def test_operational_h2h_trigger_rolls_back_provider_failure(monkeypatch):
    async def refresh_h2h(**kwargs):
        return {"error": "API error"}

    monkeypatch.setattr(matches_api.football_service, "refresh_h2h", refresh_h2h)
    db = DB()
    with pytest.raises(Exception) as raised:
        asyncio.run(refresh_h2h_route(20, 10, db))
    assert getattr(raised.value, "status_code", None) == 502
    assert db.commits == 0
    assert db.rollbacks == 1


def test_operational_h2h_trigger_rolls_back_projection_failure(monkeypatch):
    async def refresh_h2h(**kwargs):
        raise ValueError("projection failure")

    monkeypatch.setattr(matches_api.football_service, "refresh_h2h", refresh_h2h)
    db = DB()
    with pytest.raises(Exception, match="projection failure"):
        asyncio.run(refresh_h2h_route(20, 10, db))
    assert db.commits == 0
    assert db.rollbacks == 1
