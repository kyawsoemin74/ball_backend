from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.league import League


class LeagueRepository:
    async def get_by_id(self, db: AsyncSession, league_id: int) -> League | None:
        result = await db.execute(select(League).where(League.league_id == league_id))
        return result.scalar_one_or_none()

    async def get_many_by_ids(self, db: AsyncSession, league_ids: list[int]) -> list[League]:
        if not league_ids:
            return []
        result = await db.execute(select(League).where(League.league_id.in_(league_ids)))
        return list(result.scalars().all())
