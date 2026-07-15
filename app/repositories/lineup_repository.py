from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.match import Match
from app.models.match_lineup import MatchLineup


class LineupRepository:
    async def get_match_status(self, db: AsyncSession, match_id: int) -> Match | None:
        result = await db.execute(select(Match).where(Match.match_id == match_id))
        return result.scalar_one_or_none()

    async def get_by_match_id(self, db: AsyncSession, match_id: int) -> MatchLineup | None:
        result = await db.execute(select(MatchLineup).where(MatchLineup.match_id == match_id))
        return result.scalar_one_or_none()

    async def create_one(self, db: AsyncSession, match_id: int, data: list[dict]) -> MatchLineup:
        record = MatchLineup(match_id=match_id, data=data)
        db.add(record)
        return record

    async def update_one(self, db: AsyncSession, record: MatchLineup, data: list[dict]) -> MatchLineup:
        record.data = data
        record.updated_at = datetime.now(timezone.utc)
        return record

    async def upsert_one(self, db: AsyncSession, match_id: int, data: list[dict]) -> MatchLineup:
        existing = await self.get_by_match_id(db, match_id)
        if existing is None:
            return await self.create_one(db, match_id, data)
        return await self.update_one(db, existing, data)
