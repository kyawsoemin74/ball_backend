from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.league_identity_recovery import LeagueIdentityRecovery


class LeagueIdentityRecoveryRepository:
    async def get_by_league_id(
        self,
        db: AsyncSession,
        league_id: int,
    ) -> LeagueIdentityRecovery | None:
        result = await db.execute(
            select(LeagueIdentityRecovery).where(
                LeagueIdentityRecovery.league_id == int(league_id)
            )
        )
        return result.scalar_one_or_none()

    async def get_retry_candidates(
        self,
        db: AsyncSession,
        now: datetime,
        limit: int = 100,
    ) -> list[LeagueIdentityRecovery]:
        result = await db.execute(
            select(LeagueIdentityRecovery)
            .where(LeagueIdentityRecovery.state == "IDENTITY_RESOLUTION_RETRY")
            .where(LeagueIdentityRecovery.next_retry_at <= now)
            .where(LeagueIdentityRecovery.attempt_count < 5)
            .order_by(LeagueIdentityRecovery.next_retry_at.asc(), LeagueIdentityRecovery.league_id.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def ensure_required(
        self,
        db: AsyncSession,
        league_id: int,
    ) -> LeagueIdentityRecovery:
        record = await self.get_by_league_id(db, league_id)
        if record is None:
            record = LeagueIdentityRecovery(
                league_id=int(league_id),
                state="IDENTITY_RESOLUTION_REQUIRED",
                attempt_count=0,
            )
            db.add(record)
            await db.flush()
        elif record.state == "IDENTITY_RESOLVED":
            record.state = "IDENTITY_RESOLUTION_REQUIRED"
            record.next_retry_at = None
            record.last_error_code = None
            record.last_error_message = None
            await db.flush()
        return record

    async def mark_running(
        self,
        db: AsyncSession,
        record: LeagueIdentityRecovery,
        attempted_at: datetime,
    ) -> LeagueIdentityRecovery:
        record.state = "IDENTITY_RESOLUTION_RUNNING"
        record.attempt_count += 1
        record.last_attempted_at = attempted_at
        record.next_retry_at = None
        await db.flush()
        return record

    async def mark_resolved(
        self,
        db: AsyncSession,
        record: LeagueIdentityRecovery,
        provider: str,
        provider_id: str,
        resolved_at: datetime,
    ) -> LeagueIdentityRecovery:
        record.state = "IDENTITY_RESOLVED"
        record.provider = provider
        record.resolved_provider_id = provider_id
        record.resolved_at = resolved_at
        record.next_retry_at = None
        record.last_error_code = None
        record.last_error_message = None
        await db.flush()
        return record

    async def mark_failure(
        self,
        db: AsyncSession,
        record: LeagueIdentityRecovery,
        *,
        state: str,
        error_code: str,
        message: str,
        next_retry_at: datetime | None,
    ) -> LeagueIdentityRecovery:
        record.state = state
        record.last_error_code = error_code
        record.last_error_message = message[:2000]
        record.next_retry_at = next_retry_at
        await db.flush()
        return record
