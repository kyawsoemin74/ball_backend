import asyncio

import app.services.active_match_service as active_module
from app.services.active_match_service import ActiveMatchService


class FakeRedis:
    def __init__(self):
        self.store = {}
        self.set_calls = []

    async def set(self, key, value, ex=None):
        self.store[key] = value
        self.set_calls.append((key, value, ex))

    async def get(self, key):
        return self.store.get(key)

    async def scan_iter(self, match=None):
        prefix = (match or "").replace("*", "")
        for key in list(self.store.keys()):
            if key.startswith(prefix):
                yield key


class FlakyGetRedis(FakeRedis):
    async def get(self, key):
        return None


def test_mark_match_active_creates_redis_key_with_ttl(monkeypatch):
    fake_redis = FakeRedis()
    monkeypatch.setattr(active_module, "async_redis", fake_redis)

    service = ActiveMatchService()
    ttl = asyncio.run(service.mark_match_active(1539017))

    assert ttl == 300
    assert fake_redis.set_calls == [("fover:active_match:1539017", "1", 300)]


def test_mark_match_active_duplicate_heartbeat_refreshes_ttl(monkeypatch):
    fake_redis = FakeRedis()
    monkeypatch.setattr(active_module, "async_redis", fake_redis)

    service = ActiveMatchService()
    asyncio.run(service.mark_match_active(1539017))
    asyncio.run(service.mark_match_active(1539017))

    assert len(fake_redis.set_calls) == 2
    assert fake_redis.set_calls[0][0] == "fover:active_match:1539017"
    assert fake_redis.set_calls[1][0] == "fover:active_match:1539017"
    assert fake_redis.set_calls[0][2] == 300
    assert fake_redis.set_calls[1][2] == 300


def test_get_active_matches_returns_sorted_unique_ids(monkeypatch):
    fake_redis = FakeRedis()
    fake_redis.store = {
        "fover:active_match:200": "1",
        "fover:active_match:100": "1",
        "fover:active_match:100": "1",
        "fover:other:1": "1",
    }
    monkeypatch.setattr(active_module, "async_redis", fake_redis)

    service = ActiveMatchService()
    result = asyncio.run(service.get_active_matches())

    assert result == [100, 200]


def test_get_active_matches_handles_expired_keys(monkeypatch):
    fake_redis = FlakyGetRedis()
    fake_redis.store = {"fover:active_match:123": "1"}
    monkeypatch.setattr(active_module, "async_redis", fake_redis)

    service = ActiveMatchService()
    result = asyncio.run(service.get_active_matches())

    assert result == []
