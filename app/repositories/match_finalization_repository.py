from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.match_finalization import MatchFinalization


FINALIZATION_MAX_ATTEMPTS = 5
FINALIZATION_RETRY_DELAYS = (
    timedelta(minutes=15),
    timedelta(minutes=30),
    timedelta(minutes=60),
    timedelta(minutes=120),
)
FINALIZATION_WAIT_RETRY_DELAYS = (
    timedelta(minutes=5),
    timedelta(minutes=10),
    timedelta(minutes=15),
    timedelta(minutes=30),
)
FINALIZATION_RUNNING_STALE_AFTER = timedelta(minutes=5)


class MatchFinalizationRepository:
    async def get_by_match_id(
        self,
        db: AsyncSession,
        match_id: int,
        *,
        for_update: bool = False,
    ) -> MatchFinalization | None:
        query = select(MatchFinalization).where(MatchFinalization.match_id == int(match_id))
        if for_update:
            query = query.with_for_update()
        result = await db.execute(query)
        return result.scalar_one_or_none()

    async def ensure_pending(
        self,
        db: AsyncSession,
        match_id: int,
        *,
        now: datetime | None = None,
    ) -> MatchFinalization:
        record = await self.get_by_match_id(db, match_id, for_update=True)
        if record is None:
            statement = (
                pg_insert(MatchFinalization)
                .values(
                    match_id=int(match_id),
                    state="PENDING",
                    attempt_count=0,
                    next_retry_at=now or datetime.now(timezone.utc),
                )
                .on_conflict_do_nothing(index_elements=[MatchFinalization.match_id])
            )
            await db.execute(statement)
            record = await self.get_by_match_id(db, match_id, for_update=True)
            if record is None:
                raise RuntimeError(
                    f"Could not persist finalization eligibility for Match {match_id}."
                )
        return record

    async def get_due_match_ids(
        self,
        db: AsyncSession,
        now: datetime,
        *,
        limit: int = 100,
    ) -> list[int]:
        stale_before = now - FINALIZATION_RUNNING_STALE_AFTER
        result = await db.execute(
            select(MatchFinalization.match_id)
            .where(
                or_(
                    and_(
                        MatchFinalization.state.in_(("PENDING", "FAILED")),
                        or_(
                            MatchFinalization.next_retry_at.is_(None),
                            MatchFinalization.next_retry_at <= now,
                        ),
                    ),
                    and_(
                        MatchFinalization.state == "RUNNING",
                        or_(
                            MatchFinalization.last_attempted_at.is_(None),
                            MatchFinalization.last_attempted_at <= stale_before,
                        ),
                    ),
                )
            )
            .order_by(MatchFinalization.next_retry_at, MatchFinalization.match_id)
            .limit(limit)
        )
        return [int(row[0]) for row in result.all()]

    async def mark_running(
        self,
        db: AsyncSession,
        record: MatchFinalization,
        now: datetime,
    ) -> None:
        record.state = "RUNNING"
        record.attempt_count += 1
        record.last_attempted_at = now
        record.next_retry_at = None
        record.completed_at = None
        record.last_error_category = None
        record.last_error_message = None
        await db.flush()

    async def mark_failure(
        self,
        db: AsyncSession,
        record: MatchFinalization,
        *,
        now: datetime,
        category: str,
        message: str,
        retryable: bool,
    ) -> None:
        record.last_error_category = category[:50]
        record.last_error_message = message[:2000]
        record.completed_at = None
        if (
            not retryable
            or record.attempt_count < 1
            or record.attempt_count >= FINALIZATION_MAX_ATTEMPTS
        ):
            record.state = "EXHAUSTED"
            record.next_retry_at = None
        else:
            record.state = "FAILED"
            record.next_retry_at = now + FINALIZATION_RETRY_DELAYS[record.attempt_count - 1]
        await db.flush()

    async def mark_waiting(
        self,
        db: AsyncSession,
        record: MatchFinalization,
        *,
        now: datetime,
    ) -> None:
        record.completed_at = None
        record.last_error_category = None
        record.last_error_message = None
        if (
            record.attempt_count < 1
            or record.attempt_count >= FINALIZATION_MAX_ATTEMPTS
        ):
            record.state = "EXHAUSTED"
            record.next_retry_at = None
        else:
            record.state = "PENDING"
            record.next_retry_at = (
                now + FINALIZATION_WAIT_RETRY_DELAYS[record.attempt_count - 1]
            )
        await db.flush()

    async def mark_success(
        self,
        db: AsyncSession,
        record: MatchFinalization,
        completed_at: datetime,
    ) -> None:
        record.state = "SUCCESS"
        record.next_retry_at = None
        record.completed_at = completed_at
        record.last_error_category = None
        record.last_error_message = None
        await db.flush()
