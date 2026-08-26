"""
Venue Read Service - Read-only access to Venue Master.

Responsibilities:
  - Provide read access to Venues
  - Delegate to Repository
  - No database writes
  - No external API calls
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.venue import Venue
from app.repositories.venue_repository import VenueRepository


class VenueService:
    """Read-only service for Venue Master."""

    def __init__(self) -> None:
        self.repository = VenueRepository()

    async def get_venue(self, db: AsyncSession, venue_id: int) -> Venue | None:
        """Get Venue by local ID."""
        return await self.repository.get_by_id(db, venue_id)

    async def get_venue_by_provider_id(self, db: AsyncSession, provider_id: str, provider: str = "api-football") -> Venue | None:
        """Get Venue by (provider, provider_id)."""
        return await self.repository.get_by_provider_id(db, provider_id, provider)

    async def get_venues_by_provider_ids(self, db: AsyncSession, provider_ids: list[str], provider: str = "api-football") -> list[Venue]:
        """Get multiple Venues by provider IDs."""
        return await self.repository.get_many_by_provider_ids(db, provider_ids, provider)

    async def get_all_venues(self, db: AsyncSession) -> list[Venue]:
        """Get all Venues."""
        return await self.repository.get_all(db)

    async def get_venue_count(self, db: AsyncSession) -> int:
        """Get total Venue count."""
        return await self.repository.count(db)
