from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.match import Match
from app.models.match_lineup_finalization import MatchLineupFinalization


FINAL_LINEUP_REQUIRED_STATES = ("REQUIRED", "RETRYABLE")
FINAL_LINEUP_TERMINAL_STATUSES = ("FT", "AET", "PEN")


class FinalLineupFinalizationRepository:
    async def get_by_match_id(self, db: AsyncSession, match_id: int) -> MatchLineupFinalization | None:
        result = await db.execute(
            select(MatchLineupFinalization).where(MatchLineupFinalization.match_id == match_id)
        )
        return result.scalar_one_or_none()

    async def create_required(
        self,
        db: AsyncSession,
        match_id: int,
        required_at: datetime | None = None,
    ) -> MatchLineupFinalization:
        record = await self.get_by_match_id(db, match_id)
        if record is not None:
            return record

        record = MatchLineupFinalization(
            match_id=match_id,
            status="REQUIRED",
            attempt_count=0,
            required_at=required_at or datetime.now(timezone.utc),
        )
        db.add(record)
        await db.flush()
        return record

    async def mark_attempt_started(
        self,
        db: AsyncSession,
        record: MatchLineupFinalization,
        attempted_at: datetime,
    ) -> MatchLineupFinalization:
        if record.status == "SUCCESS":
            return record
        record.attempt_count += 1
        record.last_attempted_at = attempted_at
        record.updated_at = attempted_at
        await db.flush()
        return record

    async def mark_retryable(
        self,
        db: AsyncSession,
        record: MatchLineupFinalization,
        failure_category: str,
        failure_reason: str | None,
        attempted_at: datetime,
    ) -> MatchLineupFinalization:
        if record.status == "SUCCESS":
            return record
        record.status = "RETRYABLE"
        record.completed_at = None
        record.failure_category = failure_category
        record.failure_reason = self._truncate_reason(failure_reason)
        record.last_attempted_at = attempted_at
        record.updated_at = datetime.now(timezone.utc)
        await db.flush()
        return record

    async def mark_success(
        self,
        db: AsyncSession,
        record: MatchLineupFinalization,
        completed_at: datetime,
    ) -> MatchLineupFinalization:
        if record.status == "SUCCESS":
            return record
        record.status = "SUCCESS"
        record.completed_at = completed_at
        record.failure_category = None
        record.failure_reason = None
        record.updated_at = completed_at
        await db.flush()
        return record

    async def get_retry_candidates(self, db: AsyncSession, limit: int) -> list[MatchLineupFinalization]:
        result = await db.execute(
            select(MatchLineupFinalization)
            .join(Match, Match.fixture_id == MatchLineupFinalization.match_id)
            .where(MatchLineupFinalization.status.in_(FINAL_LINEUP_REQUIRED_STATES))
            .where(Match.status.in_(FINAL_LINEUP_TERMINAL_STATUSES))
            .order_by(
                MatchLineupFinalization.required_at.asc(),
                MatchLineupFinalization.match_id.asc(),
            )
            .limit(limit)
        )
        return list(result.scalars().all())

    @staticmethod
    def _truncate_reason(reason: str | None) -> str | None:
        if reason is None:
            return None
        return str(reason)[:500]