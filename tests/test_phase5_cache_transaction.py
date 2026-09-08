import asyncio
from types import SimpleNamespace

from app import cache as cache_module
from app.services.fixture_sync_service import FixtureSyncService


class CacheResult:
    def __init__(self, value):
        self.value = value

    async def get(self, key):
        return self.value


class EmptyRepository:
    async def get_allowed_ids(self, db):
        return {39}


class FakeProvider:
    async def get_fixtures_by_date(self, target_date):
        return {"response": []}


class FakeDB:
    pass


def test_cache_hit_deserializes_cached_value(monkeypatch):
    async def fake_get(_key):
        return '{"value": 7}'

    monkeypatch.setattr(cache_module.async_redis, "get", fake_get)
    assert asyncio.run(cache_module.cache_get_json("fover:test:hit")) == {"value": 7}


def test_cache_miss_returns_none_and_redis_failure_is_fail_open(monkeypatch):
    async def fake_get(_key):
        return None

    monkeypatch.setattr(cache_module.async_redis, "get", fake_get)
    assert asyncio.run(cache_module.cache_get_json("fover:test:miss")) is None

    async def failing_get(_key):
        raise RuntimeError("redis unavailable")

    monkeypatch.setattr(cache_module.async_redis, "get", failing_get)
    assert asyncio.run(cache_module.cache_get_json("fover:test:failure")) is None


def test_cache_write_failure_does_not_change_database_outcome(monkeypatch):
    async def failing_set(*args, **kwargs):
        raise RuntimeError("redis unavailable")

    monkeypatch.setattr(cache_module.async_redis, "set", failing_set)
    asyncio.run(cache_module.cache_set_json("fover:test:write", {"value": 1}, 60))


def test_fixture_sync_empty_response_does_not_publish_active_cache_state(monkeypatch):
    service = FixtureSyncService(
        client=SimpleNamespace(),
        team_service=SimpleNamespace(),
        fixture_provider=FakeProvider(),
    )
    service.allowed_league_repository = EmptyRepository()
    service._defer_live_cache_invalidation = True

    result = asyncio.run(service.sync_daily_fixtures(FakeDB(), "2026-06-15"))

    assert result["success"] is True
    assert result.get("active_match_updates", {}) == {}


def test_post_commit_active_updates_are_explicitly_applied(monkeypatch):
    calls = {"marked": [], "removed": []}

    class ActiveService:
        async def mark_match_active(self, match_id):
            calls["marked"].append(match_id)

        async def remove_match_active(self, match_id):
            calls["removed"].append(match_id)

    service = FixtureSyncService.__new__(FixtureSyncService)
    monkeypatch.setattr("app.services.fixture_sync_service.active_match_service", ActiveService())
    asyncio.run(service.apply_active_match_updates({10: "1H", 11: "FT"}))

    assert calls == {"marked": [10], "removed": [11]}
