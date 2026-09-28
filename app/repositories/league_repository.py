from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.league import League
from app.models.match import Match

MM_TZ = timezone(timedelta(hours=6, minutes=30))


class LeagueRepository:
    async def find_by_provider_identity(
        self,
        db: AsyncSession,
        provider: str,
        provider_id: str | int | None,
    ) -> League | None:
        if provider is None or provider_id is None:
            return None
        provider_id_key = str(provider_id)
        result = await db.execute(
            select(League).where(
                (League.provider == provider) & (League.provider_id == provider_id_key)
            )
        )
        rows = result.scalars().all()
        if len(rows) > 1:
            raise ValueError(
                f"Multiple leagues found for provider={provider} "
                f"provider_id={provider_id_key}"
            )
        return rows[0] if rows else None

    async def create_registered(
        self,
        db: AsyncSession,
        row: dict,
    ) -> League:
        row = dict(row)
        row.pop("league_id", None)
        league = League(**row)
        db.add(league)
        await db.flush()
        await db.refresh(league)
        return league

    async def get_by_id(
        self,
        db: AsyncSession,
        league_id: int,
        allowed_ids: set[int] | None = None,
    ) -> League | None:
        query = select(League).where(League.league_id == league_id)
        if allowed_ids is not None:
            if not allowed_ids:
                return None
            query = query.where(League.league_id.in_(allowed_ids))
        result = await db.execute(query)
        return result.scalar_one_or_none()

    async def attach_provider_identity(
        self,
        db: AsyncSession,
        league_id: int,
        provider: str,
        provider_id: str | int,
    ) -> League:
        league = await self.get_by_id(db, league_id)
        if league is None:
            raise ValueError(f"League not found for league_id={league_id}")
        league.provider = str(provider).strip()
        league.provider_id = str(provider_id).strip()
        await db.flush()
        return league

    async def get_many_by_ids(
        self,
        db: AsyncSession,
        league_ids: list[int],
        allowed_ids: set[int] | None = None,
    ) -> list[League]:
        if not league_ids:
            return []
        query = select(League).where(League.league_id.in_(league_ids))
        if allowed_ids is not None:
            if not allowed_ids:
                return []
            query = query.where(League.league_id.in_(allowed_ids))
        result = await db.execute(query)
        return list(result.scalars().all())

    async def get_all_leagues(
        self,
        db: AsyncSession,
        allowed_ids: set[int] | None = None,
    ) -> list[League]:
        query = select(League)
        if allowed_ids is not None:
            if not allowed_ids:
                return []
            query = query.where(League.league_id.in_(allowed_ids))
        query = query.order_by(
            League.display_order.asc(),
            League.name.asc(),
            League.league_id.asc(),
        )
        result = await db.execute(query)
        return list(result.scalars().all())

    async def get_featured_leagues(
        self,
        db: AsyncSession,
        allowed_ids: set[int] | None = None,
    ) -> list[League]:
        query = select(League).where(League.is_featured.is_(True))
        if allowed_ids is not None:
            if not allowed_ids:
                return []
            query = query.where(League.league_id.in_(allowed_ids))
        query = query.order_by(
            League.display_order.asc(),
            League.name.asc(),
            League.league_id.asc(),
        )
        result = await db.execute(query)
        return list(result.scalars().all())

    async def get_leagues_with_matches_today(
        self,
        db: AsyncSession,
        allowed_ids: set[int] | None = None,
    ) -> list[League]:
        today = datetime.now(MM_TZ).date()
        start_dt = (
            datetime.combine(today, datetime.min.time())
            - timedelta(hours=6, minutes=30)
        ).replace(tzinfo=timezone.utc)
        end_dt = start_dt + timedelta(days=1)

        query = (
            select(League)
            .join(Match, Match.league_id == League.league_id)
            .where(Match.match_time >= start_dt)
            .where(Match.match_time < end_dt)
        )
        if allowed_ids is not None:
            if not allowed_ids:
                return []
            query = query.where(League.league_id.in_(allowed_ids))

        query = query.distinct().order_by(
            League.display_order.asc(),
            League.name.asc(),
            League.league_id.asc(),
        )
        result = await db.execute(query)
        return list(result.scalars().all())

    async def upsert_one(self, db: AsyncSession, row: dict) -> League:
        insert_stmt = pg_insert(League).values(row)
        upsert_stmt = insert_stmt.on_conflict_do_update(
            constraint="leagues_pkey",
            set_={
                "name": insert_stmt.excluded.name,
                "country": insert_stmt.excluded.country,
                "country_code": insert_stmt.excluded.country_code,
                "logo": insert_stmt.excluded.logo,
                "season": insert_stmt.excluded.season,
                "is_featured": insert_stmt.excluded.is_featured,
                "provider": insert_stmt.excluded.provider,
                "type": insert_stmt.excluded.type,
                "national": insert_stmt.excluded.national,
                "country_id": insert_stmt.excluded.country_id,
            },
        )
        await db.execute(upsert_stmt)

        result = await db.execute(
            select(League).where(League.league_id == int(row["league_id"]))
        )
        league = result.scalar_one_or_none()
        if league is None:
            raise RuntimeError(f"League upsert failed for league_id={row['league_id']}")
        return league

    async def update_country_id(
        self,
        db: AsyncSession,
        league_id: int,
        country_id: int | None,
    ) -> None:
        stmt = update(League).where(League.league_id == league_id).values(country_id=country_id)
        await db.execute(stmt)

    async def update_provider_identity(
        self,
        db: AsyncSession,
        league_id: int,
        provider: str,
        provider_id: str | int,
    ) -> None:
        stmt = (
            update(League)
            .where(League.league_id == league_id)
            .values(provider=provider, provider_id=str(provider_id))
        )
        await db.execute(stmt)
