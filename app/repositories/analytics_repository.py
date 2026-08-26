from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.analytics import (
    AnalyticsH2HSnapshot,
    AnalyticsMatchLineup,
    AnalyticsMatchOddsSnapshot,
    AnalyticsMatchTeamStatistic,
    AnalyticsTeamSeasonStanding,
)


class AnalyticsStatisticsRepository:
    model = AnalyticsMatchTeamStatistic

    async def list_by_match(self, db: AsyncSession, match_id: int):
        result = await db.execute(select(self.model).where(self.model.match_id == match_id))
        return list(result.scalars().all())

    async def replace_by_match(self, db: AsyncSession, match_id: int, rows: list[dict]) -> None:
        await db.execute(delete(self.model).where(self.model.match_id == match_id))
        db.add_all([self.model(match_id=match_id, **row) for row in rows])


class AnalyticsStandingRepository:
    model = AnalyticsTeamSeasonStanding

    async def list_by_season(self, db: AsyncSession, league_season_id: int):
        result = await db.execute(select(self.model).where(self.model.league_season_id == league_season_id))
        return list(result.scalars().all())

    async def replace_by_season(self, db: AsyncSession, league_season_id: int, rows: list[dict]) -> None:
        await db.execute(delete(self.model).where(self.model.league_season_id == league_season_id))
        db.add_all([self.model(league_season_id=league_season_id, **row) for row in rows])


class AnalyticsOddsRepository:
    model = AnalyticsMatchOddsSnapshot

    async def list_by_match(self, db: AsyncSession, match_id: int):
        result = await db.execute(select(self.model).where(self.model.match_id == match_id))
        return list(result.scalars().all())

    async def replace_by_match(self, db: AsyncSession, match_id: int, rows: list[dict]) -> None:
        await db.execute(delete(self.model).where(self.model.match_id == match_id))
        db.add_all([self.model(match_id=match_id, **row) for row in rows])


class AnalyticsH2HRepository:
    model = AnalyticsH2HSnapshot

    async def list_by_pair(self, db: AsyncSession, team_low_id: int, team_high_id: int):
        result = await db.execute(
            select(self.model).where(
                self.model.team_low_id == team_low_id,
                self.model.team_high_id == team_high_id,
            )
        )
        return list(result.scalars().all())

    async def replace_by_pair(self, db: AsyncSession, team_low_id: int, team_high_id: int, rows: list[dict]) -> None:
        await db.execute(
            delete(self.model).where(
                self.model.team_low_id == team_low_id,
                self.model.team_high_id == team_high_id,
            )
        )
        db.add_all([
            self.model(team_low_id=team_low_id, team_high_id=team_high_id, **row)
            for row in rows
        ])


class AnalyticsLineupRepository:
    model = AnalyticsMatchLineup

    async def list_by_match(self, db: AsyncSession, match_id: int):
        result = await db.execute(select(self.model).where(self.model.match_id == match_id))
        return list(result.scalars().all())

    async def replace_by_match(self, db: AsyncSession, match_id: int, rows: list[dict]) -> None:
        await db.execute(delete(self.model).where(self.model.match_id == match_id))
        db.add_all([self.model(match_id=match_id, **row) for row in rows])