from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.team import Team


class TeamRepository:
    async def find_by_provider_identity(
        self,
        db: AsyncSession,
        provider: str,
        provider_id: str | int | None,
    ) -> Team | None:
        if not provider or provider_id is None:
            return None

        provider_id_key = str(provider_id)
        result = await db.execute(
            select(Team).where(
                Team.provider == provider,
                Team.provider_id == provider_id_key,
            )
        )
        rows = list(result.scalars().all())
        if len(rows) > 1:
            raise ValueError(f"Multiple teams found for provider={provider} provider_id={provider_id_key}")
        return rows[0] if rows else None

    async def get_null_provider_api_football_teams(self, db: AsyncSession) -> list[Team]:
        result = await db.execute(
            select(Team)
            .where(Team.provider == "api-football")
            .where(Team.provider_id.is_(None))
            .order_by(Team.team_id)
        )
        return list(result.scalars().all())

    async def get_by_id(self, db: AsyncSession, team_id: int) -> Team | None:
        result = await db.execute(select(Team).where(Team.team_id == team_id))
        return result.scalar_one_or_none()

    async def get_many_by_ids(self, db: AsyncSession, team_ids: list[int]) -> list[Team]:
        if not team_ids:
            return []
        result = await db.execute(select(Team).where(Team.team_id.in_(team_ids)))
        return list(result.scalars().all())

    async def list_supported_provider_teams(
        self,
        db: AsyncSession,
        *,
        league_ids: set[int],
        seasons: dict[int, str],
        provider: str = "api-football",
    ) -> list[Team]:
        if not league_ids:
            return []
        result = await db.execute(
            select(Team)
            .where(Team.provider == provider)
            .where(Team.provider_id.is_not(None))
            .where(Team.current_league_id.in_(league_ids))
            .order_by(Team.team_id)
        )
        return [
            team for team in result.scalars().all()
            if str(getattr(team, "current_season", "")) == str(seasons.get(int(team.current_league_id), ""))
        ]

    async def find_candidate_null_provider_teams(
        self,
        db: AsyncSession,
        provider: str,
        name: str | None = None,
        country: str | None = None,
    ) -> list[Team]:
        if not provider:
            return []

        stmt = select(Team).where(
            Team.provider == provider,
            Team.provider_id.is_(None),
        )
        if name and name.strip():
            normalized_name = name.strip()
            stmt = stmt.where(Team.name == normalized_name)
        if country and country.strip():
            stmt = stmt.where(Team.country == country.strip())

        result = await db.execute(stmt.order_by(Team.team_id))
        return list(result.scalars().all())

    async def attach_provider_identity(
        self,
        db: AsyncSession,
        team_id: int,
        provider: str,
        provider_id: str | int,
    ) -> Team:
        provider_id_key = str(provider_id)
        await db.execute(
            update(Team)
            .where(Team.team_id == team_id)
            .values(provider=provider, provider_id=provider_id_key)
        )
        await db.flush()
        return await self.get_by_id(db, team_id)

    async def release_provider_identity(
        self,
        db: AsyncSession,
        team_id: int,
    ) -> Team | None:
        await db.execute(
            update(Team)
            .where(Team.team_id == team_id)
            .values(provider_id=None)
        )
        await db.flush()
        return await self.get_by_id(db, team_id)

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

    async def update_country_id(
        self,
        db: AsyncSession,
        team_id: int,
        country_id: int | None,
    ) -> None:
        stmt = update(Team).where(Team.team_id == team_id).values(country_id=country_id)
        await db.execute(stmt)

    async def update_provider_metadata(
        self,
        db: AsyncSession,
        team_id: int,
        *,
        name: str | None = None,
        country: str | None = None,
        logo: str | None = None,
        stadium: str | None = None,
        founded: int | None = None,
    ) -> None:
        values = {
            "name": name,
            "country": country,
            "logo": logo,
            "stadium": stadium,
            "founded": founded,
        }
        await db.execute(update(Team).where(Team.team_id == team_id).values(**values))

    async def update_current_coach(self, db: AsyncSession, team_id: int, coach_id: int | None) -> None:
        if coach_id is None:
            return
        await db.execute(
            update(Team)
            .where(Team.team_id == team_id)
            .values(coach_id=coach_id)
        )
        await db.flush()

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

    async def upsert_by_provider_identity(
        self,
        db: AsyncSession,
        row: dict,
    ) -> Team:
        provider = row.get("provider")
        provider_id = row.get("provider_id")
        if not provider or provider_id is None:
            raise ValueError("Team provider identity is required")

        insert_stmt = pg_insert(Team).values(
            {
                **row,
                "provider": provider,
                "provider_id": str(provider_id),
            }
        )
        upsert_stmt = insert_stmt.on_conflict_do_update(
            constraint="uq_teams_provider_provider_id",
            set_={
                "name": insert_stmt.excluded.name,
                "country": insert_stmt.excluded.country,
                "logo": insert_stmt.excluded.logo,
                "stadium": insert_stmt.excluded.stadium,
                "founded": insert_stmt.excluded.founded,
            },
        )
        await db.execute(upsert_stmt)

        team = await self.find_by_provider_identity(db, provider, provider_id)
        if team is None:
            raise RuntimeError(
                "Team upsert failed for provider="
                f"{provider} provider_id={provider_id}"
            )
        return team
