from datetime import date, datetime, timedelta, timezone

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.models.league import League
from app.models.match import Match


class MatchRepository:
    async def get_many_by_ids(self, db: AsyncSession, match_ids: list[int]) -> list[Match]:
        if not match_ids:
            return []
        result = await db.execute(select(Match).where(Match.match_id.in_(match_ids)))
        return list(result.scalars().all())

    async def get_matches_by_date(self, db: AsyncSession, date_val: date) -> list[Match]:
        start_dt = (datetime.combine(date_val, datetime.min.time()) - timedelta(hours=6, minutes=30)).replace(tzinfo=timezone.utc)
        end_dt = start_dt + timedelta(days=1)

        result = await db.execute(
            select(Match)
            .join(League, Match.league_id == League.league_id)
            .options(joinedload(Match.league_obj))
            .where(Match.match_time >= start_dt)
            .where(Match.match_time < end_dt)
            .where(or_(League.display_order <= 200, League.is_featured.is_(True)))
            .order_by(
                League.is_featured.desc(),
                League.display_order.asc(),
                League.country.asc(),
                League.name.asc(),
                Match.match_time.asc(),
                Match.match_id.asc(),
            )
        )
        return list(result.scalars().all())

    @staticmethod
    def is_visible_league(match: Match) -> bool:
        league = getattr(match, "league_obj", None)
        if league is None:
            return True

        return bool(league.display_order <= 200 or league.is_featured)

    @staticmethod
    def order_matches_for_date(matches: list[Match]) -> list[Match]:
        return sorted(
            matches,
            key=lambda match: (
                0 if bool(getattr(getattr(match, "league_obj", None), "is_featured", False)) else 1,
                int(getattr(getattr(match, "league_obj", None), "display_order", 999) or 999),
                str(getattr(getattr(match, "league_obj", None), "country", "")).lower(),
                str(getattr(getattr(match, "league_obj", None), "name", "")).lower(),
                getattr(match, "match_time", None) or datetime.min.replace(tzinfo=timezone.utc),
            ),
        )

    async def get_live_stale(self, db: AsyncSession, live_ids: set[int], stale_threshold) -> list[Match]:
        from app.services.football import LIVE_STATUSES

        query = select(Match).where(Match.status.in_(LIVE_STATUSES), Match.match_time >= stale_threshold)
        if live_ids:
            query = query.where(Match.match_id.not_in(list(live_ids)))
        result = await db.execute(query)
        return list(result.scalars().all())
