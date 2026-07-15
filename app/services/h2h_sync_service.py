import logging
from typing import TYPE_CHECKING

from app.providers.h2h_provider import H2HProvider
from app.repositories.h2h_repository import H2HRepository
from app.services.cache_service import CacheService

if TYPE_CHECKING:
    from app.services.h2h_service import H2HService

logger = logging.getLogger(__name__)


class H2HSyncService:
    """Write/refresh orchestration for H2H data without owning read or transport logic."""

    def __init__(
        self,
        h2h_service: "H2HService | None",
        cache_service: CacheService | None = None,
        h2h_provider: H2HProvider | None = None,
        h2h_repository: H2HRepository | None = None,
    ) -> None:
        self.h2h_service = h2h_service
        self.cache_service = cache_service or CacheService()
        self.h2h_provider = h2h_provider
        self.h2h_repository = h2h_repository or H2HRepository()

    async def refresh_h2h(self, db, h2h_key: str) -> dict:
        api_res = await self.h2h_provider.get_h2h_by_key(h2h_key)
        if not api_res or "response" not in api_res:
            return {"error": "API error"}

        h2h_data = api_res["response"]
        await self.h2h_repository.upsert_one(db, h2h_key, h2h_data)
        await db.flush()

        return {"source": "api", "data": h2h_data, "cached": False, "updated": True}
