from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.models.league import League
from app.models.match import Match


class MatchRepository:
    async def get_many_by_ids(self, db: AsyncSession, match_ids: list[int], allowed_ids: set[int] | None = None) -> list[Match]:
        if not match_ids:
            return []
        query = select(Match).where(Match.match_id.in_(match_ids))
        if allowed_ids is not None:
            if not allowed_ids:
                return []
            query = query.where(Match.league_id.in_(allowed_ids))
        result = await db.execute(query)
        return list(result.scalars().all())

    async def get_matches_by_date(self, db: AsyncSession, date_val: date, allowed_ids: set[int] | None = None) -> list[Match]:
        start_dt = (datetime.combine(date_val, datetime.min.time()) - timedelta(hours=6, minutes=30)).replace(tzinfo=timezone.utc)
        end_dt = start_dt + timedelta(days=1)

        query = (
            select(Match)
            .join(League, Match.league_id == League.league_id)
            .options(joinedload(Match.league_obj))
            .where(Match.match_time >= start_dt)
            .where(Match.match_time < end_dt)
        )
        if allowed_ids is not None:
            if not allowed_ids:
                return []
            query = query.where(Match.league_id.in_(allowed_ids))

        result = await db.execute(
            query.order_by(
                League.is_featured.desc(),
                League.display_order.asc(),
                League.country.asc(),
                League.name.asc(),
                Match.match_time.asc(),
                Match.match_id.asc(),
            )
        )
        return list(result.scalars().all())

    async def get_by_id(self, db: AsyncSession, match_id: int, allowed_ids: set[int] | None = None) -> Match | None:
        query = select(Match).options(joinedload(Match.league_obj)).where(Match.match_id == match_id)
        if allowed_ids is not None:
            if not allowed_ids:
                return None
            query = query.where(Match.league_id.in_(allowed_ids))
        result = await db.execute(query)
        return result.scalar_one_or_none()

    async def get_live_matches(self, db: AsyncSession, allowed_ids: set[int] | None = None) -> list[Match]:
        from app.services.football import LIVE_STATUSES

        query = select(Match).where(Match.status.in_(LIVE_STATUSES))
        if allowed_ids is not None:
            if not allowed_ids:
                return []
            query = query.where(Match.league_id.in_(allowed_ids))

        result = await db.execute(query)
        return list(result.scalars().all())

    async def get_all_matches(self, db: AsyncSession, allowed_ids: set[int] | None = None, status: str | None = None, league_id: int | None = None, skip: int = 0, limit: int = 100) -> list[Match]:
        query = select(Match)
        if status:
            query = query.where(Match.status == status)
        if league_id:
            query = query.where(Match.league_id == league_id)
        if allowed_ids is not None:
            if not allowed_ids:
                return []
            query = query.where(Match.league_id.in_(allowed_ids))
        result = await db.execute(query.offset(skip).limit(limit))
        return list(result.scalars().all())

    async def get_team_matches(self, db: AsyncSession, team_id: int, allowed_ids: set[int] | None = None) -> list[Match]:
        query = select(Match).where((Match.home_team_id == team_id) | (Match.away_team_id == team_id))
        if allowed_ids is not None:
            if not allowed_ids:
                return []
            query = query.where(Match.league_id.in_(allowed_ids))
        result = await db.execute(query.order_by(Match.match_time.asc(), Match.match_id.asc()))
        return list(result.scalars().all())

    async def get_team_matches_recent(self, db: AsyncSession, team_id: int, allowed_ids: set[int] | None = None) -> list[Match]:
        query = select(Match).where((Match.home_team_id == team_id) | (Match.away_team_id == team_id))
        if allowed_ids is not None:
            if not allowed_ids:
                return []
            query = query.where(Match.league_id.in_(allowed_ids))
        result = await db.execute(query.order_by(Match.match_time.desc(), Match.match_id.desc()))
        return list(result.scalars().all())

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
