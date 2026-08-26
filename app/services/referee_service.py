from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.referee import Referee
from app.repositories.referee_repository import RefereeRepository


class RefereeService:
    """Read-only service for Referee Master."""

    def __init__(self, referee_repository: Optional[RefereeRepository] = None) -> None:
        self.referee_repository = referee_repository or RefereeRepository()

    async def get_referee(self, db: AsyncSession, referee_id: int) -> Optional[Referee]:
        return await self.referee_repository.get_by_id(db, referee_id)

    async def get_referee_by_provider_id(
        self,
        db: AsyncSession,
        provider_id: str,
        provider: str = "api-football",
    ) -> Optional[Referee]:
        return await self.referee_repository.get_by_provider_id(db, provider_id, provider)

    async def get_referee_by_normalized_name(
        self,
        db: AsyncSession,
        normalized_name: str,
        provider: str = "api-football",
    ) -> Optional[Referee]:
        return await self.referee_repository.get_by_normalized_name(db, normalized_name, provider)

    async def get_all_referees(self, db: AsyncSession) -> list[Referee]:
        return await self.referee_repository.get_all(db)

    async def get_referee_count(self, db: AsyncSession) -> int:
        return await self.referee_repository.count(db)
