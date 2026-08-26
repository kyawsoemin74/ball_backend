from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.coach import Coach
from app.repositories.coach_repository import CoachRepository


class CoachService:
    """Read-only service for Coach Master."""

    def __init__(self, coach_repository: Optional[CoachRepository] = None) -> None:
        self.coach_repository = coach_repository or CoachRepository()

    async def get_coach(self, db: AsyncSession, coach_id: int) -> Optional[Coach]:
        return await self.coach_repository.get_by_id(db, coach_id)

    async def get_coach_by_provider_id(
        self,
        db: AsyncSession,
        provider_id: str,
        provider: str = "api-football",
    ) -> Optional[Coach]:
        return await self.coach_repository.get_by_provider_id(db, provider_id, provider)

    async def get_coach_by_normalized_name(
        self,
        db: AsyncSession,
        normalized_name: str,
        provider: str = "api-football",
    ) -> Optional[Coach]:
        return await self.coach_repository.get_by_normalized_name(db, normalized_name, provider)

    async def get_all_coaches(self, db: AsyncSession) -> list[Coach]:
        return await self.coach_repository.get_all(db)

    async def get_coach_count(self, db: AsyncSession) -> int:
        return await self.coach_repository.count(db)
