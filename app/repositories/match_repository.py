from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.match import Match


class MatchRepository:
    async def get_many_by_ids(self, db: AsyncSession, match_ids: list[int]) -> list[Match]:
        if not match_ids:
            return []
        result = await db.execute(select(Match).where(Match.match_id.in_(match_ids)))
        return list(result.scalars().all())

    async def get_live_stale(self, db: AsyncSession, live_ids: set[int], stale_threshold) -> list[Match]:
        query = select(Match).where(Match.status.in_("1H", "2H", "HT", "ET", "LIVE", "BT", "P"), Match.match_time >= stale_threshold)
        if live_ids:
            query = query.where(Match.match_id.not_in(list(live_ids)))
        result = await db.execute(query)
        return list(result.scalars().all())
