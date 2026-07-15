from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.match_statistics import MatchStatistics


class StatisticsRepository:
    async def get_by_match_id(self, db: AsyncSession, match_id: int) -> MatchStatistics | None:
        result = await db.execute(select(MatchStatistics).where(MatchStatistics.match_id == match_id))
        return result.scalar_one_or_none()

    async def replace_match_statistics(self, db: AsyncSession, match_id: int, data: list[dict]) -> MatchStatistics:
        existing = await self.get_by_match_id(db, match_id)
        if existing is None:
            record = MatchStatistics(match_id=match_id, data=data)
            db.add(record)
            return record

        existing.data = data
        return existing

    async def delete_by_match_id(self, db: AsyncSession, match_id: int) -> None:
        existing = await self.get_by_match_id(db, match_id)
        if existing is not None:
            await db.delete(existing)
