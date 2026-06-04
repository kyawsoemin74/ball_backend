from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.team import Team


class TeamRepository:
    async def get_by_id(self, db: AsyncSession, team_id: int) -> Team | None:
        result = await db.execute(select(Team).where(Team.team_id == team_id))
        return result.scalar_one_or_none()

    async def get_many_by_ids(self, db: AsyncSession, team_ids: list[int]) -> list[Team]:
        if not team_ids:
            return []
        result = await db.execute(select(Team).where(Team.team_id.in_(team_ids)))
        return list(result.scalars().all())
