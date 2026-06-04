from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.standing import Standings


class StandingRepository:
    async def get_for_league_season(self, db: AsyncSession, league_id: int, season: str | int) -> list[Standings]:
        result = await db.execute(
            select(Standings)
            .where(Standings.league_id == league_id, Standings.season == str(season))
            .order_by(Standings.position)
        )
        return list(result.scalars().all())

    async def delete_for_league_season(self, db: AsyncSession, league_id: int, season: str | int) -> None:
        await db.execute(delete(Standings).where(Standings.league_id == league_id, Standings.season == str(season)))
