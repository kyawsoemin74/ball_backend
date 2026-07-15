from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
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

    async def upsert_many(self, db: AsyncSession, rows: list[dict]) -> None:
        if not rows:
            return

        insert_stmt = pg_insert(Team).values(rows)
        upsert_stmt = insert_stmt.on_conflict_do_update(
            constraint="teams_pkey",
            set_={
                "name": insert_stmt.excluded.name,
                "country": insert_stmt.excluded.country,
                "logo": insert_stmt.excluded.logo,
                "stadium": insert_stmt.excluded.stadium,
                "founded": insert_stmt.excluded.founded,
            },
        )
        await db.execute(upsert_stmt)

    async def update_team_context(
        self,
        db: AsyncSession,
        team_id: int,
        *,
        current_league_id: int | None = None,
        current_season: str | None = None,
    ) -> None:
        values = {}
        if current_league_id is not None:
            values["current_league_id"] = current_league_id
        if current_season is not None:
            values["current_season"] = current_season
        if not values:
            return

        stmt = update(Team).where(Team.team_id == team_id).values(**values)
        await db.execute(stmt)

    async def upsert_one(self, db: AsyncSession, row: dict) -> Team:
        insert_stmt = pg_insert(Team).values(row)
        upsert_stmt = insert_stmt.on_conflict_do_update(
            constraint="teams_pkey",
            set_={
                "name": insert_stmt.excluded.name,
                "country": insert_stmt.excluded.country,
                "logo": insert_stmt.excluded.logo,
                "stadium": insert_stmt.excluded.stadium,
                "founded": insert_stmt.excluded.founded,
            },
        )
        await db.execute(upsert_stmt)

        result = await db.execute(select(Team).where(Team.team_id == int(row["team_id"])))
        team = result.scalar_one_or_none()
        if team is None:
            raise RuntimeError(f"Team upsert failed for team_id={row['team_id']}")
        return team
