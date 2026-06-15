from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.allowed_league import AllowedLeague
from app.models.league import League


class AllowedLeagueRepository:
    async def get_all(self, db: AsyncSession) -> list[dict]:
        result = await db.execute(
            select(
                AllowedLeague.league_id,
                League.name.label("league_name"),
                League.country,
            )
            .join(League, League.league_id == AllowedLeague.league_id, isouter=True)
            .order_by(AllowedLeague.league_id.asc())
        )
        return [
            {
                "league_id": row.league_id,
                "league_name": row.league_name,
                "country": row.country,
            }
            for row in result.all()
        ]

    async def get_by_league_id(self, db: AsyncSession, league_id: int) -> AllowedLeague | None:
        result = await db.execute(select(AllowedLeague).where(AllowedLeague.league_id == league_id))
        return result.scalar_one_or_none()

    async def create(self, db: AsyncSession, league_id: int) -> AllowedLeague:
        allowed_league = AllowedLeague(league_id=league_id)
        try:
            db.add(allowed_league)
            await db.flush()
            await db.refresh(allowed_league)
            await db.commit()
            return allowed_league
        except Exception:
            await db.rollback()
            raise

    async def delete(self, db: AsyncSession, allowed_league: AllowedLeague) -> None:
        try:
            await db.delete(allowed_league)
            await db.flush()
            await db.commit()
        except Exception:
            await db.rollback()
            raise
