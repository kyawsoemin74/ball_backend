from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.league_season import LeagueSeason
from app.repositories.league_season_repository import LeagueSeasonRepository


class LeagueSeasonService:
    def __init__(self, repository: LeagueSeasonRepository | None = None):
        self.repository = repository or LeagueSeasonRepository()

    async def get_by_league_and_season(self, db: AsyncSession, league_id: int, season: str | int) -> LeagueSeason | None:
        return await self.repository.get_by_league_and_season(db, league_id, season)

    async def list_by_league(self, db: AsyncSession, league_id: int) -> list[LeagueSeason]:
        return await self.repository.list_by_league(db, league_id)

    async def get_current_season(self, db: AsyncSession, league_id: int) -> LeagueSeason | None:
        return await self.repository.get_current_season(db, league_id)

    async def resolve_current_season(self, db: AsyncSession, league_id: int) -> LeagueSeason | None:
        record = await self.get_current_season(db, league_id)
        if record is not None:
            return record

        seasons = await self.list_by_league(db, league_id)
        if not seasons:
            return None
        return seasons[0]
