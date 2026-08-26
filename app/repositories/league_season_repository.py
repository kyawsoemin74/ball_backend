from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.league_season import LeagueSeason


class LeagueSeasonRepository:
    async def get_by_id(self, db: AsyncSession, season_id: int) -> LeagueSeason | None:
        result = await db.execute(select(LeagueSeason).where(LeagueSeason.id == season_id))
        return result.scalar_one_or_none()

    async def get_by_league_and_season(self, db: AsyncSession, league_id: int, season: str | int) -> LeagueSeason | None:
        season_text = str(season)
        result = await db.execute(
            select(LeagueSeason).where(LeagueSeason.league_id == league_id, LeagueSeason.season == season_text)
        )
        return result.scalar_one_or_none()

    async def list_by_league(self, db: AsyncSession, league_id: int) -> list[LeagueSeason]:
        result = await db.execute(select(LeagueSeason).where(LeagueSeason.league_id == league_id).order_by(LeagueSeason.season.desc()))
        return list(result.scalars().all())

    async def get_current_season(self, db: AsyncSession, league_id: int) -> LeagueSeason | None:
        result = await db.execute(
            select(LeagueSeason)
            .where(LeagueSeason.league_id == league_id)
            .where(LeagueSeason.current.is_(True))
            .order_by(LeagueSeason.updated_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def upsert_one(self, db: AsyncSession, row: dict) -> LeagueSeason:
        if "league_id" not in row or "season" not in row:
            raise ValueError("LeagueSeason row requires league_id and season")

        insert_stmt = pg_insert(LeagueSeason).values(row)
        upsert_stmt = insert_stmt.on_conflict_do_update(
            index_elements=[LeagueSeason.league_id, LeagueSeason.season],
            set_={
                "provider": insert_stmt.excluded.provider,
                "provider_id": insert_stmt.excluded.provider_id,
                "start_date": insert_stmt.excluded.start_date,
                "end_date": insert_stmt.excluded.end_date,
                "current": insert_stmt.excluded.current,
            },
        )
        await db.execute(upsert_stmt)

        season_record = await self.get_by_league_and_season(db, int(row["league_id"]), row["season"])
        if season_record is None:
            raise RuntimeError(f"LeagueSeason upsert failed for league_id={row['league_id']} season={row['season']}")
        return season_record
