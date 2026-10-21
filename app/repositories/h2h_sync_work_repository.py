from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy import and_, case, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.h2h_sync_work import H2HSyncWork
from app.models.match import Match


class H2HSyncWorkRepository:
    async def enqueue_discovered(self, db: AsyncSession, matches: list[Match]) -> int:
        enqueued = 0
        for match in matches:
            pair_ids = sorted((int(match.home_team_id), int(match.away_team_id)))
            insert_stmt = pg_insert(H2HSyncWork).values(
                local_match_id=int(match.local_match_id),
                pair_key=f"{pair_ids[0]}-{pair_ids[1]}",
                match_time=match.match_time,
                status="PENDING",
                attempt_count=0,
                retryable=True,
                next_retry_at=None,
            )
            inserted = await db.execute(
                insert_stmt.on_conflict_do_nothing(
                    index_elements=[H2HSyncWork.local_match_id]
                ).returning(H2HSyncWork.local_match_id)
            )
            if inserted.scalar_one_or_none() is not None:
                enqueued += 1
                continue

            existing_status = H2HSyncWork.status
            terminal = existing_status.in_(("SUCCESS", "EMPTY"))
            processing = existing_status == "PROCESSING"
            await db.execute(
                update(H2HSyncWork)
                .where(H2HSyncWork.local_match_id == int(match.local_match_id))
                .values(
                    pair_key=case(
                        (processing, H2HSyncWork.pair_key),
                        else_=f"{pair_ids[0]}-{pair_ids[1]}",
                    ),
                    match_time=case(
                        (processing, H2HSyncWork.match_time),
                        else_=match.match_time,
                    ),
                    status=case((terminal, "PENDING"), else_=existing_status),
                    retryable=case((terminal, True), else_=H2HSyncWork.retryable),
                    next_retry_at=case((terminal, None), else_=H2HSyncWork.next_retry_at),
                    completed_at=case((terminal, None), else_=H2HSyncWork.completed_at),
                    updated_at=func.now(),
                )
            )
        await db.flush()
        return enqueued

    async def recover_expired_processing(self, db: AsyncSession, now: datetime) -> int:
        result = await db.execute(
            update(H2HSyncWork)
            .where(
                H2HSyncWork.status == "PROCESSING",
                H2HSyncWork.lease_expires_at <= now,
            )
            .values(
                status="PENDING",
                retryable=True,
                next_retry_at=now,
                claimed_at=None,
                lease_expires_at=None,
                claim_token=None,
                updated_at=now,
            )
            .execution_options(synchronize_session=False)
        )
        return int(result.rowcount or 0)

    async def count_due(self, db: AsyncSession, now: datetime) -> int:
        result = await db.execute(
            select(func.count())
            .select_from(H2HSyncWork)
            .where(
                H2HSyncWork.status.in_(("PENDING", "FAILED")),
                or_(
                    H2HSyncWork.status == "PENDING",
                    and_(
                        H2HSyncWork.retryable.is_(True),
                        or_(
                            H2HSyncWork.next_retry_at.is_(None),
                            H2HSyncWork.next_retry_at <= now,
                        ),
                    ),
                ),
            )
        )
        return int(result.scalar_one())

    async def claim_due(
        self,
        db: AsyncSession,
        *,
        now: datetime,
        lease_expires_at: datetime,
        upcoming: bool,
        limit: int,
    ) -> list[dict]:
        time_condition = (
            H2HSyncWork.match_time >= now
            if upcoming
            else H2HSyncWork.match_time < now
        )
        due_condition = or_(
            H2HSyncWork.status == "PENDING",
            and_(
                H2HSyncWork.status == "FAILED",
                H2HSyncWork.retryable.is_(True),
                or_(
                    H2HSyncWork.next_retry_at.is_(None),
                    H2HSyncWork.next_retry_at <= now,
                ),
            ),
        )
        result = await db.execute(
            select(H2HSyncWork)
            .where(time_condition, due_condition)
            .order_by(H2HSyncWork.match_time.asc(), H2HSyncWork.local_match_id.asc())
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        records = list(result.scalars().all())
        claimed = []
        for record in records:
            token = uuid4()
            record.status = "PROCESSING"
            record.claimed_at = now
            record.lease_expires_at = lease_expires_at
            record.claim_token = token
            record.next_retry_at = None
            record.updated_at = now
            claimed.append(
                {
                    "local_match_id": int(record.local_match_id),
                    "pair_key": record.pair_key,
                    "match_time": record.match_time,
                    "claim_token": token,
                }
            )
        await db.flush()
        return claimed

    async def get_by_local_match_id(
        self,
        db: AsyncSession,
        local_match_id: int,
    ) -> H2HSyncWork | None:
        result = await db.execute(
            select(H2HSyncWork).where(H2HSyncWork.local_match_id == int(local_match_id))
        )
        return result.scalar_one_or_none()

    async def _finish_claim(
        self,
        db: AsyncSession,
        local_match_id: int,
        claim_token,
        *,
        status: str,
        now: datetime,
        outcome_count: int | None,
        last_error_code: str | None = None,
        last_error: str | None = None,
    ) -> bool:
        result = await db.execute(
            update(H2HSyncWork)
            .where(
                H2HSyncWork.local_match_id == int(local_match_id),
                H2HSyncWork.status == "PROCESSING",
                H2HSyncWork.claim_token == claim_token,
            )
            .values(
                status=status,
                attempt_count=H2HSyncWork.attempt_count + 1,
                retryable=status == "FAILED",
                last_attempt_at=now,
                next_retry_at=None,
                claimed_at=None,
                lease_expires_at=None,
                claim_token=None,
                completed_at=now if status in ("SUCCESS", "EMPTY") else None,
                last_outcome_count=outcome_count,
                last_error_code=(last_error_code[:100] if last_error_code else None),
                last_error=(last_error[:4000] if last_error else None),
                updated_at=now,
            )
            .execution_options(synchronize_session=False)
        )
        return result.rowcount == 1

    async def mark_success(
        self,
        db: AsyncSession,
        local_match_id: int,
        claim_token,
        now: datetime,
        outcome_count: int,
    ) -> bool:
        return await self._finish_claim(
            db,
            local_match_id,
            claim_token,
            status="SUCCESS",
            now=now,
            outcome_count=outcome_count,
        )

    async def mark_empty(
        self,
        db: AsyncSession,
        local_match_id: int,
        claim_token,
        now: datetime,
    ) -> bool:
        return await self._finish_claim(
            db,
            local_match_id,
            claim_token,
            status="EMPTY",
            now=now,
            outcome_count=0,
        )

    async def mark_failed(
        self,
        db: AsyncSession,
        local_match_id: int,
        claim_token,
        now: datetime,
        error_code: str,
        error_message: str,
        retry_delay_seconds: int,
        outcome_count: int | None = None,
    ) -> bool:
        result = await db.execute(
            update(H2HSyncWork)
            .where(
                H2HSyncWork.local_match_id == int(local_match_id),
                H2HSyncWork.status == "PROCESSING",
                H2HSyncWork.claim_token == claim_token,
            )
            .values(
                status="FAILED",
                attempt_count=H2HSyncWork.attempt_count + 1,
                retryable=True,
                last_attempt_at=now,
                next_retry_at=now + timedelta(seconds=retry_delay_seconds),
                claimed_at=None,
                lease_expires_at=None,
                claim_token=None,
                completed_at=None,
                last_outcome_count=outcome_count,
                last_error_code=error_code[:100],
                last_error=error_message[:4000],
                updated_at=now,
            )
            .execution_options(synchronize_session=False)
        )
        return result.rowcount == 1

    async def release_claim(
        self,
        db: AsyncSession,
        local_match_id: int,
        claim_token,
        now: datetime,
    ) -> bool:
        result = await db.execute(
            update(H2HSyncWork)
            .where(
                H2HSyncWork.local_match_id == int(local_match_id),
                H2HSyncWork.status == "PROCESSING",
                H2HSyncWork.claim_token == claim_token,
            )
            .values(
                status="PENDING",
                retryable=True,
                next_retry_at=now,
                claimed_at=None,
                lease_expires_at=None,
                claim_token=None,
                updated_at=now,
            )
            .execution_options(synchronize_session=False)
        )
        return result.rowcount == 1

    async def complete_manual(
        self,
        db: AsyncSession,
        *,
        local_match_id: int,
        pair_key: str,
        match_time: datetime,
        now: datetime,
        outcome_count: int,
    ) -> list[int]:
        status = "EMPTY" if outcome_count == 0 else "SUCCESS"
        insert_stmt = pg_insert(H2HSyncWork).values(
            local_match_id=int(local_match_id),
            pair_key=pair_key,
            match_time=match_time,
            status=status,
            attempt_count=1,
            retryable=False,
            last_attempt_at=now,
            completed_at=now,
            last_outcome_count=outcome_count,
            updated_at=now,
        )
        await db.execute(
            insert_stmt.on_conflict_do_update(
                index_elements=[H2HSyncWork.local_match_id],
                set_={
                    "pair_key": insert_stmt.excluded.pair_key,
                    "match_time": insert_stmt.excluded.match_time,
                    "status": status,
                    "attempt_count": H2HSyncWork.attempt_count + 1,
                    "retryable": False,
                    "last_attempt_at": now,
                    "next_retry_at": None,
                    "claimed_at": None,
                    "lease_expires_at": None,
                    "claim_token": None,
                    "completed_at": now,
                    "last_outcome_count": outcome_count,
                    "last_error_code": None,
                    "last_error": None,
                    "updated_at": now,
                },
            )
        )
        await db.flush()
        return await self.complete_pair_siblings(
            db,
            pair_key=pair_key,
            exclude_local_match_id=local_match_id,
            outcome=status,
            now=now,
            outcome_count=outcome_count,
        )

    async def complete_pair_siblings(
        self,
        db: AsyncSession,
        *,
        pair_key: str,
        exclude_local_match_id: int,
        outcome: str,
        now: datetime,
        outcome_count: int,
        selected_claims: list[dict] | None = None,
    ) -> list[int]:
        selected_claim_conditions = [
            and_(
                H2HSyncWork.local_match_id == int(claim["local_match_id"]),
                H2HSyncWork.claim_token == claim["claim_token"],
            )
            for claim in (selected_claims or [])
            if int(claim["local_match_id"]) != int(exclude_local_match_id)
        ]
        result = await db.execute(
            update(H2HSyncWork)
            .where(
                H2HSyncWork.pair_key == pair_key,
                H2HSyncWork.local_match_id != int(exclude_local_match_id),
                or_(
                    H2HSyncWork.status.in_(("PENDING", "FAILED")),
                    and_(
                        H2HSyncWork.status == "PROCESSING",
                        or_(*selected_claim_conditions) if selected_claim_conditions else False,
                    ),
                ),
            )
            .values(
                status=outcome,
                retryable=False,
                next_retry_at=None,
                claimed_at=None,
                lease_expires_at=None,
                claim_token=None,
                completed_at=now,
                last_outcome_count=outcome_count,
                last_error_code=None,
                last_error=None,
                last_attempt_at=now,
                updated_at=now,
            )
            .returning(H2HSyncWork.local_match_id)
            .execution_options(synchronize_session=False)
        )
        return [int(row[0]) for row in result.all()]
