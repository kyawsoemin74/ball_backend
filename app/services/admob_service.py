from sqlalchemy.ext.asyncio import AsyncSession

from app.cache import make_cache_key
from app.core.config import settings
from app.models.ad_config import AdConfig
from app.repositories.ad_repository import AdRepository
from app.services.cache_service import CacheService


class AdMobService:
    """Thin orchestration service for AdMob configuration persistence."""

    def __init__(self, repository: AdRepository | None = None, cache_service=None) -> None:
        self.repository = repository or AdRepository()
        self.cache_service = cache_service or CacheService()

    @staticmethod
    def _to_cache_payload(config: AdConfig | None) -> dict | None:
        if config is None:
            return None
        return {
            "id": config.id,
            "is_enabled": config.is_enabled,
            "banner_android": config.banner_android,
            "banner_ios": config.banner_ios,
            "interstitial_android": config.interstitial_android,
            "interstitial_ios": config.interstitial_ios,
            "rewarded_android": config.rewarded_android,
            "rewarded_ios": config.rewarded_ios,
            "app_open_android": config.app_open_android,
            "app_open_ios": config.app_open_ios,
            "created_at": config.created_at,
            "updated_at": config.updated_at,
        }

    @staticmethod
    def _from_cache_payload(payload: dict | None) -> AdConfig | None:
        if payload is None:
            return None
        return AdConfig(**payload)

    async def get_current_config(self, db: AsyncSession) -> AdConfig | None:
        """Return the most recent ad configuration record, if one exists."""
        cache_key = make_cache_key("admob", "config")
        try:
            cached = await self.cache_service.get_json(cache_key)
        except Exception:
            cached = None

        if cached is not None:
            return self._from_cache_payload(cached)

        config = await self.repository.get_current_config(db)
        if config is None:
            return None

        try:
            await self.cache_service.set_json(cache_key, self._to_cache_payload(config), settings.REDIS_TTL_LEAGUE_TEAM)
        except Exception:
            pass
        return config

    async def update_current_config(self, db: AsyncSession, config: AdConfig) -> AdConfig:
        """Persist a provided ad configuration and return the refreshed record."""
        updated_config = await self.repository.update_current_config(db, config)
        cache_key = make_cache_key("admob", "config")
        try:
            await self.cache_service.delete(cache_key)
        except Exception:
            pass
        return updated_config
