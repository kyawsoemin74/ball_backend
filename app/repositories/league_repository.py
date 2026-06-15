from datetime import datetime, timedelta, timezone

from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.league import League
from app.models.match import Match

MM_TZ = timezone(timedelta(hours=6, minutes=30))


class LeagueRepository:
    def _visible_query(self):
        return select(League).where(
            or_(League.display_order <= 200, League.is_featured.is_(True))
        )

    async def get_by_id(self, db: AsyncSession, league_id: int) -> League | None:
        result = await db.execute(select(League).where(League.league_id == league_id))
        return result.scalar_one_or_none()

    async def get_many_by_ids(self, db: AsyncSession, league_ids: list[int]) -> list[League]:
        if not league_ids:
            return []
        result = await db.execute(select(League).where(League.league_id.in_(league_ids)))
        return list(result.scalars().all())

    async def get_all_leagues(self, db: AsyncSession) -> list[League]:
        result = await db.execute(
            self._visible_query().order_by(League.display_order.asc(), League.name.asc())
        )
        return list(result.scalars().all())

    async def get_featured_leagues(self, db: AsyncSession) -> list[League]:
        result = await db.execute(
            self._visible_query()
            .where(League.is_featured.is_(True))
            .order_by(League.display_order.asc(), League.name.asc())
        )
        return list(result.scalars().all())

    async def get_leagues_with_matches_today(self, db: AsyncSession) -> list[League]:
        today = datetime.now(MM_TZ).date()
        start_dt = (datetime.combine(today, datetime.min.time()) - timedelta(hours=6, minutes=30)).replace(tzinfo=timezone.utc)
        end_dt = start_dt + timedelta(days=1)

        result = await db.execute(
            self._visible_query()
            .join(Match, Match.league_id == League.league_id)
            .where(Match.match_time >= start_dt)
            .where(Match.match_time < end_dt)
            .distinct()
            .order_by(League.display_order.asc(), League.name.asc())
        )
        return list(result.scalars().all())
