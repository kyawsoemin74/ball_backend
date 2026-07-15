from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
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

    async def upsert_for_league_season(
        self,
        db: AsyncSession,
        league_id: int,
        season: str | int,
        rows: list[dict],
    ) -> None:
        season_text = str(season)

        if not rows:
            await self.delete_for_league_season(db, league_id, season_text)
            return

        team_ids = [int(row["team_id"]) for row in rows]
        await db.execute(
            delete(Standings).where(
                Standings.league_id == league_id,
                Standings.season == season_text,
                Standings.team_id.not_in(team_ids),
            )
        )

        insert_stmt = pg_insert(Standings).values(rows)
        upsert_stmt = insert_stmt.on_conflict_do_update(
            constraint="uq_standings_league_id_season_team_id",
            set_={
                "position": insert_stmt.excluded.position,
                "team_name": insert_stmt.excluded.team_name,
                "team_logo": insert_stmt.excluded.team_logo,
                "group_name": insert_stmt.excluded.group_name,
                "form": insert_stmt.excluded.form,
                "description": insert_stmt.excluded.description,
                "points": insert_stmt.excluded.points,
                "played": insert_stmt.excluded.played,
                "won": insert_stmt.excluded.won,
                "drawn": insert_stmt.excluded.drawn,
                "lost": insert_stmt.excluded.lost,
                "goals_for": insert_stmt.excluded.goals_for,
                "goals_against": insert_stmt.excluded.goals_against,
                "goal_difference": insert_stmt.excluded.goal_difference,
                "updated_at": insert_stmt.excluded.updated_at,
            },
        )
        await db.execute(upsert_stmt)
