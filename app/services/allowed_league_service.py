from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.allowed_league import AllowedLeague
from app.models.league import League
from app.repositories.allowed_league_repository import AllowedLeagueRepository


class AllowedLeagueService:
    def __init__(self, repository: AllowedLeagueRepository | None = None) -> None:
        self.repository = repository or AllowedLeagueRepository()

    async def list_allowed_leagues(self, db: AsyncSession) -> list[dict]:
        return await self.repository.get_all(db)

    async def add_allowed_league(self, db: AsyncSession, league_id: int) -> AllowedLeague:
        if not isinstance(league_id, int) or league_id <= 0:
            raise ValueError("league_id must be a positive integer")

        existing = await self.repository.get_by_league_id(db, league_id)
        if existing is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="League is already allowed")

        league_result = await db.execute(select(League).where(League.league_id == league_id))
        league_record = league_result.scalar_one_or_none()
        if league_record is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="League not found")

        try:
            return await self.repository.create(db, league_id)
        except IntegrityError as exc:
            await db.rollback()
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="League is already allowed") from exc

    async def remove_allowed_league(self, db: AsyncSession, league_id: int) -> None:
        if not isinstance(league_id, int) or league_id <= 0:
            raise ValueError("league_id must be a positive integer")

        allowed_league = await self.repository.get_by_league_id(db, league_id)
        if allowed_league is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Allowed league not found")

        await self.repository.delete(db, allowed_league)
