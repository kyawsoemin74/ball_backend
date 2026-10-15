from datetime import datetime

from sqlalchemy import case, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.odds_refresh_state import OddsProviderCooldown, OddsRefreshState
from app.services.odds_reconciliation import (
    ATTEMPT_IN_PROGRESS,
    COMMIT_CONFIRMED_CACHE_PENDING,
    COMMIT_NOT_APPLIED_STATE,
    COMMIT_PENDING,
    MANUAL_REVIEW,
    PENDING_RECONCILIATION_STATES,
    RESOLVED,
)


class OddsRefreshStateRepository:
    async def get_by_match_id(self, db: AsyncSession, match_id: int) -> OddsRefreshState | None:
        result = await db.execute(
            select(OddsRefreshState)
            .where(OddsRefreshState.match_id == match_id)
        )
        return result.scalar_one_or_none()

    async def get_provider_cooldown(
        self,
        db: AsyncSession,
        provider_key: str,
    ) -> OddsProviderCooldown | None:
        result = await db.execute(
            select(OddsProviderCooldown)
            .where(OddsProviderCooldown.provider_key == provider_key)
        )
        return result.scalar_one_or_none()

    async def get_pending_cache_invalidations(
        self,
        db: AsyncSession,
        limit: int = 100,
    ) -> list[OddsRefreshState]:
        result = await db.execute(
            select(OddsRefreshState)
            .where(OddsRefreshState.cache_invalidation_pending.is_(True))
            .order_by(OddsRefreshState.updated_at.asc(), OddsRefreshState.match_id.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_pending_reconciliations(
        self,
        db: AsyncSession,
        limit: int = 100,
    ) -> list[OddsRefreshState]:
        result = await db.execute(
            select(OddsRefreshState)
            .where(OddsRefreshState.reconciliation_state.in_(PENDING_RECONCILIATION_STATES))
            .order_by(OddsRefreshState.last_attempted_at.asc(), OddsRefreshState.match_id.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def begin_attempt(
        self,
        db: AsyncSession,
        match_id: int,
        *,
        attempt_id: str,
        provider_fixture_id: int,
        pre_attempt_odds_fingerprint: str,
        pre_attempt_analytics_fingerprint: str,
        attempted_at: datetime,
    ) -> None:
        table = OddsRefreshState.__table__
        statement = pg_insert(table).values(
            match_id=match_id,
            next_eligible_at=None,
            last_attempted_at=attempted_at,
            attempt_id=attempt_id,
            provider_fixture_id=provider_fixture_id,
            reconciliation_state=ATTEMPT_IN_PROGRESS,
            pre_attempt_odds_fingerprint=pre_attempt_odds_fingerprint,
            pre_attempt_analytics_fingerprint=pre_attempt_analytics_fingerprint,
            candidate_fingerprint=None,
            last_reconciliation_at=None,
            last_reconciliation_error=None,
            updated_at=attempted_at,
        )
        await db.execute(
            statement.on_conflict_do_update(
                index_elements=[table.c.match_id],
                set_={
                    "next_eligible_at": None,
                    "last_attempted_at": statement.excluded.last_attempted_at,
                    "attempt_id": statement.excluded.attempt_id,
                    "provider_fixture_id": statement.excluded.provider_fixture_id,
                    "reconciliation_state": statement.excluded.reconciliation_state,
                    "pre_attempt_odds_fingerprint": statement.excluded.pre_attempt_odds_fingerprint,
                    "pre_attempt_analytics_fingerprint": statement.excluded.pre_attempt_analytics_fingerprint,
                    "candidate_fingerprint": None,
                    "last_reconciliation_at": None,
                    "last_reconciliation_error": None,
                    "updated_at": statement.excluded.updated_at,
                },
            )
        )
        await db.flush()

    async def set_candidate_fingerprint(
        self,
        db: AsyncSession,
        match_id: int,
        *,
        attempt_id: str,
        candidate_fingerprint: str,
        updated_at: datetime,
    ) -> None:
        result = await db.execute(
            update(OddsRefreshState)
            .where(
                OddsRefreshState.match_id == match_id,
                OddsRefreshState.attempt_id == attempt_id,
                OddsRefreshState.reconciliation_state == ATTEMPT_IN_PROGRESS,
            )
            .values(
                candidate_fingerprint=candidate_fingerprint,
                reconciliation_state=COMMIT_PENDING,
                updated_at=updated_at,
            )
        )
        if result.rowcount != 1:
            raise RuntimeError("Odds refresh attempt is no longer active")
        await db.flush()

    async def resolve_reconciliation(
        self,
        db: AsyncSession,
        match_id: int,
        *,
        attempt_id: str,
        reconciliation_state: str,
        reconciled_at: datetime,
        next_eligible_at: datetime | None = None,
        classification: str | None = None,
        error: str | None = None,
    ) -> bool:
        if reconciliation_state not in {
            COMMIT_CONFIRMED_CACHE_PENDING,
            COMMIT_NOT_APPLIED_STATE,
            MANUAL_REVIEW,
            RESOLVED,
        }:
            raise ValueError("invalid Odds reconciliation state")
        result = await db.execute(
            update(OddsRefreshState)
            .where(
                OddsRefreshState.match_id == match_id,
                OddsRefreshState.attempt_id == attempt_id,
            )
            .values(
                reconciliation_state=reconciliation_state,
                next_eligible_at=next_eligible_at,
                last_failure_classification=classification[:64] if classification else None,
                last_reconciliation_at=reconciled_at,
                last_reconciliation_error=error[:64] if error else None,
                updated_at=reconciled_at,
                cache_invalidation_pending=(
                    reconciliation_state == COMMIT_CONFIRMED_CACHE_PENDING
                ),
            )
        )
        await db.flush()
        return result.rowcount == 1

    async def record_failure(
        self,
        db: AsyncSession,
        match_id: int,
        *,
        classification: str,
        attempted_at: datetime,
        next_eligible_at: datetime,
        provider_key: str,
        provider_cooldown_until: datetime | None = None,
        http_status: int | None = None,
    ) -> None:
        table = OddsRefreshState.__table__
        statement = pg_insert(table).values(
            match_id=match_id,
            failure_count=1,
            next_eligible_at=next_eligible_at,
            last_failure_classification=classification[:64],
            last_attempted_at=attempted_at,
            updated_at=attempted_at,
        )
        await db.execute(
            statement.on_conflict_do_update(
                index_elements=[table.c.match_id],
                set_={
                    "failure_count": table.c.failure_count + 1,
                    "next_eligible_at": statement.excluded.next_eligible_at,
                    "last_failure_classification": statement.excluded.last_failure_classification,
                    "last_attempted_at": statement.excluded.last_attempted_at,
                    "updated_at": statement.excluded.updated_at,
                },
            )
        )
        if provider_cooldown_until is not None:
            provider_table = OddsProviderCooldown.__table__
            provider_statement = pg_insert(provider_table).values(
                provider_key=provider_key,
                cooldown_until=provider_cooldown_until,
                last_http_status=http_status,
                last_failure_classification=classification[:64],
                updated_at=attempted_at,
            )
            await db.execute(
                provider_statement.on_conflict_do_update(
                    index_elements=[provider_table.c.provider_key],
                    set_={
                        "cooldown_until": func.greatest(
                            func.coalesce(
                                provider_table.c.cooldown_until,
                                provider_statement.excluded.cooldown_until,
                            ),
                            provider_statement.excluded.cooldown_until,
                        ),
                        "last_http_status": provider_statement.excluded.last_http_status,
                        "last_failure_classification": provider_statement.excluded.last_failure_classification,
                        "updated_at": provider_statement.excluded.updated_at,
                    },
                )
            )
        await db.flush()

    async def mark_success_pending_cache(
        self,
        db: AsyncSession,
        match_id: int,
        attempted_at: datetime,
    ) -> None:
        table = OddsRefreshState.__table__
        statement = pg_insert(table).values(
            match_id=match_id,
            failure_count=0,
            next_eligible_at=None,
            last_failure_classification=None,
            last_attempted_at=attempted_at,
            cache_invalidation_pending=True,
            updated_at=attempted_at,
        )
        await db.execute(
            statement.on_conflict_do_update(
                index_elements=[table.c.match_id],
                set_={
                    "failure_count": 0,
                    "next_eligible_at": None,
                    "last_failure_classification": None,
                    "last_attempted_at": statement.excluded.last_attempted_at,
                    "cache_invalidation_pending": True,
                    "reconciliation_state": case(
                        (table.c.candidate_fingerprint.is_not(None), COMMIT_CONFIRMED_CACHE_PENDING),
                        else_=table.c.reconciliation_state,
                    ),
                    "updated_at": statement.excluded.updated_at,
                },
            )
        )
        await db.flush()

    async def clear_cache_invalidation_pending(self, db: AsyncSession, match_id: int) -> None:
        await db.execute(
            update(OddsRefreshState)
            .where(OddsRefreshState.match_id == match_id)
            .values(
                cache_invalidation_pending=False,
                reconciliation_state=case(
                    (
                        OddsRefreshState.reconciliation_state == COMMIT_CONFIRMED_CACHE_PENDING,
                        RESOLVED,
                    ),
                    else_=OddsRefreshState.reconciliation_state,
                ),
            )
        )
        await db.flush()
