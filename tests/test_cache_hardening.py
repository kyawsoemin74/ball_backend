import asyncio

from app import cache as cache_mod


def test_cache_get_json_returns_none_on_malformed_payload(monkeypatch):
    async def fake_get(*args, **kwargs):
        return '{bad json'

    monkeypatch.setattr(cache_mod.async_redis, "get", fake_get)

    assert asyncio.run(cache_mod.cache_get_json("fover:test:bad")) is None


def test_cache_get_json_returns_none_on_redis_get_failure(monkeypatch):
    async def fake_get(*args, **kwargs):
        raise RuntimeError("redis down")

    monkeypatch.setattr(cache_mod.async_redis, "get", fake_get)

    assert asyncio.run(cache_mod.cache_get_json("fover:test:down")) is None


def test_cache_set_json_does_not_raise_on_redis_set_failure(monkeypatch):
    async def fake_set(*args, **kwargs):
        raise RuntimeError("redis down")

    monkeypatch.setattr(cache_mod.async_redis, "set", fake_set)

    asyncio.run(cache_mod.cache_set_json("fover:test:set", {"a": 1}, 60))


def test_cache_delete_does_not_raise_on_redis_delete_failure(monkeypatch):
    async def fake_delete(*args, **kwargs):
        raise RuntimeError("redis down")

    monkeypatch.setattr(cache_mod.async_redis, "delete", fake_delete)

    asyncio.run(cache_mod.cache_delete("fover:test:delete"))


def test_cache_get_json_sync_returns_none_on_failure(monkeypatch):
    def fake_get(*args, **kwargs):
        raise RuntimeError("redis down")

    monkeypatch.setattr(cache_mod.sync_redis, "get", fake_get)

    assert cache_mod.cache_get_json_sync("fover:test:sync") is None
