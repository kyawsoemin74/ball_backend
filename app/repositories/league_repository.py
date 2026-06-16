from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.league import League
from app.models.match import Match

MM_TZ = timezone(timedelta(hours=6, minutes=30))


class LeagueRepository:
    async def get_by_id(self, db: AsyncSession, league_id: int, allowed_ids: set[int] | None = None) -> League | None:
        query = select(League).where(League.league_id == league_id)
        if allowed_ids is not None:
            if not allowed_ids:
                return None
            query = query.where(League.league_id.in_(allowed_ids))
        result = await db.execute(query)
        return result.scalar_one_or_none()

    async def get_many_by_ids(self, db: AsyncSession, league_ids: list[int], allowed_ids: set[int] | None = None) -> list[League]:
        if not league_ids:
            return []
        query = select(League).where(League.league_id.in_(league_ids))
        if allowed_ids is not None:
            if not allowed_ids:
                return []
            query = query.where(League.league_id.in_(allowed_ids))
        result = await db.execute(query)
        return list(result.scalars().all())

    async def get_all_leagues(self, db: AsyncSession, allowed_ids: set[int] | None = None) -> list[League]:
        query = select(League)
        if allowed_ids is not None:
            if not allowed_ids:
                return []
            query = query.where(League.league_id.in_(allowed_ids))
        query = query.order_by(League.display_order.asc(), League.name.asc())
        result = await db.execute(query)
        return list(result.scalars().all())

    async def get_featured_leagues(self, db: AsyncSession, allowed_ids: set[int] | None = None) -> list[League]:
        query = select(League).where(League.is_featured.is_(True))
        if allowed_ids is not None:
            if not allowed_ids:
                return []
            query = query.where(League.league_id.in_(allowed_ids))
        query = query.order_by(League.display_order.asc(), League.name.asc())
        result = await db.execute(query)
        return list(result.scalars().all())

    async def get_leagues_with_matches_today(self, db: AsyncSession, allowed_ids: set[int] | None = None) -> list[League]:
        today = datetime.now(MM_TZ).date()
        start_dt = (datetime.combine(today, datetime.min.time()) - timedelta(hours=6, minutes=30)).replace(tzinfo=timezone.utc)
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

        query = query.distinct().order_by(League.display_order.asc(), League.name.asc())
        result = await db.execute(query)
        return list(result.scalars().all())
