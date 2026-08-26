import asyncio

from app.cache import make_cache_key
from app.models.ad_config import AdConfig
from app.services.admob_service import AdMobService


class FakeCacheService:
    def __init__(self):
        self.store = {}

    async def get_json(self, key: str):
        return self.store.get(key)

    async def set_json(self, key: str, value, ttl: int) -> None:
        self.store[key] = value

    async def delete(self, key: str) -> None:
        self.store.pop(key, None)


class FakeRepository:
    def __init__(self, initial=None):
        self.initial = initial
        self.get_calls = 0
        self.update_calls = 0

    async def get_current_config(self, db):
        self.get_calls += 1
        return self.initial

    async def update_current_config(self, db, config):
        self.update_calls += 1
        return config


def test_get_current_config_uses_cache_when_available():
    async def run_test():
        cache_service = FakeCacheService()
        cache_key = make_cache_key("admob", "config")
        cache_service.store[cache_key] = {
            "id": 5,
            "is_enabled": True,
            "banner_android": "cached-banner",
            "banner_ios": None,
            "interstitial_android": None,
            "interstitial_ios": None,
            "rewarded_android": None,
            "rewarded_ios": None,
            "app_open_android": None,
            "app_open_ios": None,
            "created_at": None,
            "updated_at": None,
        }
        repo = FakeRepository(initial=None)
        service = AdMobService(repository=repo, cache_service=cache_service)

        result = await service.get_current_config(db=None)

        assert result is not None
        assert result.is_enabled is True
        assert result.banner_android == "cached-banner"
        assert repo.get_calls == 0

    asyncio.run(run_test())


def test_update_current_config_does_not_invalidate_before_commit():
    async def run_test():
        cache_service = FakeCacheService()
        cache_key = make_cache_key("admob", "config")
        cache_service.store[cache_key] = {"id": 1, "is_enabled": False}
        repo = FakeRepository(initial=None)
        service = AdMobService(repository=repo, cache_service=cache_service)
        config = AdConfig(is_enabled=True, banner_android="new-banner")

        updated = await service.update_current_config(db=None, config=config)

        assert updated.is_enabled is True
        assert repo.update_calls == 1
        assert cache_service.store.get(cache_key) == {"id": 1, "is_enabled": False}

    asyncio.run(run_test())
