import logging
from datetime import datetime, timezone, timedelta
from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED, EVENT_JOB_MISSED, EVENT_JOB_SUBMITTED
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select, func, text, or_
from app.cache import make_cache_key
from app.core.config import settings
from app.db import async_session, engine
from app.models.allowed_league import AllowedLeague
from app.models.match import Match
from app.models.league_season import LeagueSeason
from app.models.match_h2h import MatchH2H
from app.models.match_event import MatchEvent
from app.models.odds import Odds
from app.models.league_identity_recovery import LeagueIdentityRecovery
from app.monitoring import SCHEDULER_JOB_ERRORS, SCHEDULER_JOB_RUNS
from app.repositories.lineup_refresh_state_repository import LineupRefreshStateRepository
from app.repositories.league_identity_recovery_repository import LeagueIdentityRecoveryRepository
from app.repositories.match_finalization_repository import (
    FINALIZATION_MAX_ATTEMPTS,
    FINALIZATION_RETRY_DELAYS,
    FINALIZATION_RUNNING_STALE_AFTER,
    MatchFinalizationRepository,
)
from app.services.active_match_service import active_match_service
from app.services.analytics_projection_service import log_projection_transaction
from app.services.cache_service import CacheService
from app.services.football import football_service, FINISHED_STATUSES, LIVE_STATUSES
from app.services.odds_sync_service import (
    CACHE_INVALIDATION_FAILED,
    CANDIDATE_READY,
    COMMIT_CONFIRMED,
    COMMIT_NOT_APPLIED,
    COMMIT_UNKNOWN,
    IDENTITY_REJECTED,
    IDENTITY_UNAVAILABLE,
    NO_DATA,
    OddsIdentityLookupError,
    OddsMatchIdentityError,
    PERSISTENCE_FAILED,
    PROVIDER_FAILED,
    RETRY_BLOCKED,
    RETRY_STATE_UNAVAILABLE,
    SUCCESS,
    VALIDATION_FAILED,
    OddsSyncService,
)
from app.services.odds_reconciliation import (
    COMMIT_CONFIRMED_CACHE_PENDING,
    COMMIT_NOT_APPLIED_STATE,
    MANUAL_REVIEW,
    PENDING_RECONCILIATION_STATES,
)
from app.services.resource_lock import run_with_resource_lock
from app.services.season_identity import normalize_season

logger = logging.getLogger(__name__)

EVENT_REFRESH_ALLOWED_STATUSES = {"1H", "HT", "2H", "LIVE"}
EVENT_REFRESH_INTERVAL_SECONDS = 300
STATISTICS_REFRESH_ALLOWED_STATUSES = {"1H", "HT", "2H", "LIVE"}
STATISTICS_REFRESH_BLOCKED_STATUSES = {"NS", "FT", "AET", "PEN", "PST", "CANC", "ABD", "AWD", "WO"}
ODDS_REFRESH_ELIGIBLE_STATUSES = {"NS", "TBD", "PST"}
ODDS_REFRESH_STOP_STATUSES = {"LIVE", "HT", "FT", "AET", "PEN", "CANC", "ABD", "AWD", "WO"}
ODDS_REFRESH_MAX_AGE = timedelta(hours=12)
ODDS_REFRESH_WINDOW_HOURS = 72

# Myanmar Timezone Offset (UTC+6:30)
MM_TZ = timezone(timedelta(hours=6, minutes=30))
LIVE_MATCH_SYNC_LOCK_KEY = 9342002
DAILY_FIXTURE_SYNC_LOCK_KEY = 9342003
REPAIR_DAILY_MATCHES_LOCK_KEY = 9342004
STANDINGS_REFRESH_LOCK_KEY = 9342001
_SCHEDULER_ADVISORY_LOCK_CONNECTIONS = "_scheduler_advisory_lock_connections"
PRE_LIVE_WINDOW = timedelta(minutes=10)
API_FOOTBALL_PROVIDER = "api-football"
NS_STATUS = "NS"
LIVE_SYNC_STOP_STATUSES = FINISHED_STATUSES | {"PST"}


async def commit_odds_refresh(db, odds_sync_service: OddsSyncService, local_match_id: int, refresh_result: dict) -> dict:
    """Commit a validated Odds candidate and verify ambiguous outcomes in a fresh session."""
    if refresh_result.get("status") != CANDIDATE_READY:
        return {"state": "failed", "reason": refresh_result.get("status", "INVALID_REFRESH_STATE")}
    context = refresh_result.get("_transaction_context", {})
    try:
        await db.commit()
    except Exception as exc:
        if not odds_sync_service.is_commit_ambiguous(exc):
            await db.rollback()
            logger.error(
                "ODDS_COMMIT_FAILED",
                extra={"local_match_id": local_match_id, "commit_state": "FAILED", "exception_type": exc.__class__.__name__},
            )
            return {"state": "failed", "status": COMMIT_NOT_APPLIED, "reason": COMMIT_NOT_APPLIED}

        try:
            await db.rollback()
        except Exception:
            logger.warning("ODDS_COMMIT_SESSION_RESET_FAILED", extra={"local_match_id": local_match_id})
        logger.error(
            "ODDS_COMMIT_UNKNOWN",
            extra={
                "local_match_id": local_match_id,
                "provider_fixture_id": context.get("provider_fixture_id"),
                "commit_state": COMMIT_UNKNOWN,
            },
        )
        try:
            async with async_session() as verification_db:
                verification = await odds_sync_service.verify_commit_unknown(
                    verification_db,
                    local_match_id,
                    context.get("provider_fixture_id"),
                    context.get("persistence_rows", []),
                )
        except Exception as verification_error:
            logger.error(
                "ODDS_COMMIT_UNKNOWN",
                extra={
                    "local_match_id": local_match_id,
                    "provider_fixture_id": context.get("provider_fixture_id"),
                    "commit_state": COMMIT_UNKNOWN,
                    "verification_error": verification_error.__class__.__name__,
                },
            )
            return {"state": "failed", "status": COMMIT_UNKNOWN, "reason": COMMIT_UNKNOWN}

        verification_state = verification.get("state")
        if verification_state == COMMIT_CONFIRMED:
            confirmed_result = dict(refresh_result)
            confirmed_result.update({"status": SUCCESS, "success": True, "commit_state": COMMIT_CONFIRMED})
            logger.info(
                "ODDS_COMMIT_CONFIRMED",
                extra={"local_match_id": local_match_id, "provider_fixture_id": context.get("provider_fixture_id"), "commit_state": COMMIT_CONFIRMED},
            )
            return {"state": "refreshed", "commit_state": COMMIT_CONFIRMED, "result": confirmed_result}

        logger.warning(
            "ODDS_COMMIT_NOT_CONFIRMED",
            extra={"local_match_id": local_match_id, "provider_fixture_id": context.get("provider_fixture_id"), "commit_state": verification_state or COMMIT_UNKNOWN},
        )
        failure_state = verification_state or COMMIT_UNKNOWN
        return {"state": "failed", "status": failure_state, "reason": failure_state}

    confirmed_result = dict(refresh_result)
    confirmed_result.update({"status": SUCCESS, "success": True, "commit_state": COMMIT_CONFIRMED})
    return {"state": "refreshed", "commit_state": COMMIT_CONFIRMED, "result": confirmed_result}


async def invalidate_odds_cache_after_commit(
    cache_service: CacheService,
    pending_matches: set[int],
    local_match_id: int,
    cache_key: str,
) -> str:
    """Invalidate only after commit and retain failed keys for scheduler recovery."""
    try:
        invalidated = await cache_service.delete(cache_key)
    except Exception:
        invalidated = False
    if invalidated:
        pending_matches.discard(local_match_id)
        logger.info("ODDS_CACHE_INVALIDATED", extra={"local_match_id": local_match_id, "state": "CACHE_INVALIDATED"})
        return "CACHE_INVALIDATED"
    pending_matches.add(local_match_id)
    logger.error(
        "ODDS_CACHE_INVALIDATION_FAILED",
        extra={"local_match_id": local_match_id, "state": CACHE_INVALIDATION_FAILED},
    )
    return CACHE_INVALIDATION_FAILED


async def persist_odds_retry_failure(db, odds_sync_service: OddsSyncService, local_match_id: int, result: dict) -> bool:
    state_update = odds_sync_service.retry_state_update(result)

    async def persist() -> None:
        await odds_sync_service.retry_state_repository.record_failure(
            db,
            local_match_id,
            **state_update,
        )
        await db.commit()

    try:
        locked, _ = await run_with_resource_lock(db, "odds", local_match_id, persist)
        if not locked:
            raise RuntimeError("Odds retry-state lock unavailable")
        odds_sync_service.mark_retry_state_persisted(local_match_id)
        return True
    except Exception as exc:
        try:
            await db.rollback()
        except Exception:
            logger.warning("ODDS_RETRY_STATE_ROLLBACK_FAILED", extra={"local_match_id": local_match_id})
        odds_sync_service.mark_retry_state_unavailable(
            local_match_id,
            exc,
            state_update.get("provider_cooldown_until"),
        )
        return False


async def reconcile_pending_odds_attempts(
    db,
    cache_service: CacheService,
    odds_sync_service: OddsSyncService,
    pending_matches: set[int],
    metrics: dict,
) -> bool:
    try:
        pending_states = await odds_sync_service.retry_state_repository.get_pending_reconciliations(db)
    except Exception as exc:
        await db.rollback()
        logger.critical(
            "ODDS_RECONCILIATION_STATE_READ_FAILED",
            extra={"failure_type": exc.__class__.__name__},
        )
        metrics["retry_state_failures"] += 1
        return False

    pending_attempts = [
        (state.match_id, state.attempt_id)
        for state in pending_states
        if state.attempt_id
        and state.reconciliation_state in PENDING_RECONCILIATION_STATES
    ]
    await db.rollback()

    for local_match_id, attempt_id in pending_attempts:
        async def reconcile() -> str:
            async with async_session() as verification_db:
                state = await odds_sync_service.retry_state_repository.get_by_match_id(
                    verification_db,
                    local_match_id,
                )
                if (
                    state is None
                    or state.attempt_id != attempt_id
                    or state.reconciliation_state not in PENDING_RECONCILIATION_STATES
                ):
                    await verification_db.rollback()
                    return "changed"

                now = datetime.now(timezone.utc)
                try:
                    decision = await odds_sync_service.reconcile_refresh_attempt(
                        verification_db,
                        state,
                    )
                except Exception as exc:
                    await verification_db.rollback()
                    async with async_session() as review_db:
                        marked = await odds_sync_service.retry_state_repository.resolve_reconciliation(
                            review_db,
                            local_match_id,
                            attempt_id=attempt_id,
                            reconciliation_state=MANUAL_REVIEW,
                            reconciled_at=now,
                            classification=COMMIT_UNKNOWN,
                            error=exc.__class__.__name__,
                        )
                        if marked:
                            await review_db.commit()
                        else:
                            await review_db.rollback()
                    logger.exception(
                        "ODDS_RECONCILIATION_VERIFICATION_FAILED",
                        extra={"local_match_id": local_match_id, "attempt_id": attempt_id},
                    )
                    return "manual_review"

                decision_state = decision["state"]
                if decision_state == COMMIT_CONFIRMED_CACHE_PENDING:
                    marked = await odds_sync_service.retry_state_repository.resolve_reconciliation(
                        verification_db,
                        local_match_id,
                        attempt_id=attempt_id,
                        reconciliation_state=COMMIT_CONFIRMED_CACHE_PENDING,
                        reconciled_at=now,
                    )
                    if not marked:
                        await verification_db.rollback()
                        return "changed"
                    await verification_db.commit()
                    return "confirmed_pending"

                if decision_state == COMMIT_NOT_APPLIED_STATE:
                    marked = await odds_sync_service.retry_state_repository.resolve_reconciliation(
                        verification_db,
                        local_match_id,
                        attempt_id=attempt_id,
                        reconciliation_state=COMMIT_NOT_APPLIED_STATE,
                        reconciled_at=now,
                        next_eligible_at=odds_sync_service.next_scheduler_attempt(now),
                        classification=COMMIT_NOT_APPLIED,
                    )
                    if not marked:
                        await verification_db.rollback()
                        return "changed"
                    await verification_db.commit()
                    return "not_applied"

                marked = await odds_sync_service.retry_state_repository.resolve_reconciliation(
                    verification_db,
                    local_match_id,
                    attempt_id=attempt_id,
                    reconciliation_state=MANUAL_REVIEW,
                    reconciled_at=now,
                    classification=COMMIT_UNKNOWN,
                    error=decision.get("reason"),
                )
                if marked:
                    await verification_db.commit()
                else:
                    await verification_db.rollback()
                return "manual_review"

        try:
            locked, outcome = await run_with_resource_lock(
                db,
                "odds",
                local_match_id,
                reconcile,
            )
            if not locked:
                await db.rollback()
                metrics["blocked_matches"] += 1
                continue
            if outcome == "confirmed":
                pending_matches.discard(local_match_id)
            elif outcome == "confirmed_pending":
                pending_matches.add(local_match_id)
            elif outcome == "manual_review":
                metrics["blocked_matches"] += 1
            elif outcome == "not_applied":
                logger.warning(
                    "ODDS_COMMIT_NOT_APPLIED_RECONCILED",
                    extra={"local_match_id": local_match_id, "attempt_id": attempt_id},
                )
        except Exception as exc:
            await db.rollback()
            metrics["retry_state_failures"] += 1
            odds_sync_service.mark_retry_state_unavailable(local_match_id, exc)
            logger.exception(
                "ODDS_RECONCILIATION_FAILED",
                extra={"local_match_id": local_match_id, "attempt_id": attempt_id},
            )
    return True


async def recover_pending_odds_cache(
    db,
    cache_service: CacheService,
    odds_sync_service: OddsSyncService,
    pending_matches: set[int],
    metrics: dict,
) -> bool:
    """Recover committed cache invalidations independently of the prematch window."""
    try:
        pending_states = await odds_sync_service.retry_state_repository.get_pending_cache_invalidations(db)
    except Exception as exc:
        await db.rollback()
        logger.critical(
            "ODDS_RETRY_STATE_READ_FAILED",
            extra={"failure_type": exc.__class__.__name__, "durable_deferral": False},
        )
        metrics["retry_state_failures"] += 1
        return False

    pending_match_ids = [pending_state.match_id for pending_state in pending_states]
    await db.rollback()
    for local_match_id in pending_match_ids:

        async def recover() -> str:
            current = await odds_sync_service.retry_state_repository.get_by_match_id(db, local_match_id)
            if current is None or not current.cache_invalidation_pending:
                await db.rollback()
                return "not_pending"
            if (
                current.reconciliation_state != COMMIT_CONFIRMED_CACHE_PENDING
                or current.last_reconciliation_at is None
                or current.last_failure_classification == COMMIT_UNKNOWN
            ):
                await db.rollback()
                return "blocked"
            try:
                invalidated = await cache_service.delete(make_cache_key("match", local_match_id, "odds"))
            except Exception:
                invalidated = False
            if not invalidated:
                await db.rollback()
                return "failed"
            await odds_sync_service.retry_state_repository.clear_cache_invalidation_pending(db, local_match_id)
            await db.commit()
            return "recovered"

        try:
            locked, recovered = await run_with_resource_lock(db, "odds", local_match_id, recover)
            if not locked:
                await db.rollback()
                metrics["skipped_matches"] += 1
                continue
            if recovered == "blocked":
                metrics["blocked_matches"] += 1
                pending_matches.add(local_match_id)
            elif recovered == "failed":
                pending_matches.add(local_match_id)
                metrics["cache_failures"] += 1
                logger.error(
                    "ODDS_CACHE_INVALIDATION_FAILED",
                    extra={"local_match_id": local_match_id, "state": CACHE_INVALIDATION_FAILED},
                )
            elif recovered in {"recovered", "not_pending"}:
                pending_matches.discard(local_match_id)
        except Exception as exc:
            await db.rollback()
            odds_sync_service.mark_retry_state_unavailable(local_match_id, exc)
            pending_matches.add(local_match_id)
            metrics["retry_state_failures"] += 1
            logger.exception("ODDS_CACHE_RECOVERY_FAILED", extra={"local_match_id": local_match_id})
    return True

class LiveUpdateScheduler:
    EXPECTED_JOB_IDS = (
        "sync_live_matches",
        "recover_league_identities",
        "sync_daily_fixtures",
        "repair_daily_matches",
        "refresh_standings",
        "refresh_odds",
        "refresh_lineups",
        "refresh_events",
        "refresh_statistics",
        "refresh_h2h",
    )

    def __init__(self):
        self.scheduler = AsyncIOScheduler()
        self.scheduler.add_listener(
            self._handle_scheduler_event,
            EVENT_JOB_ERROR | EVENT_JOB_EXECUTED | EVENT_JOB_MISSED | EVENT_JOB_SUBMITTED,
        )
        self.is_running = False
        self.lineup_refresh_state_repository = LineupRefreshStateRepository()
        self.match_finalization_repository = MatchFinalizationRepository()
        self.cache_service = CacheService()
        self.pending_odds_cache_invalidations: set[int] = set()

    @staticmethod
    def _handle_scheduler_event(event) -> None:
        job_id = getattr(event, "job_id", "unknown")
        if event.code == EVENT_JOB_SUBMITTED:
            logger.info("SCHEDULER_JOB_STARTED job=%s", job_id)
        elif event.code == EVENT_JOB_EXECUTED:
            result = getattr(event, "retval", None)
            summary = {}
            if isinstance(result, dict):
                for key in ("success", "updated", "synced_matches", "partial_matches", "failed_matches", "processed_matches"):
                    if key in result:
                        summary[key] = result[key]
            logger.info("SCHEDULER_JOB_COMPLETED job=%s result=%s", job_id, summary)
        elif event.code == EVENT_JOB_MISSED:
            logger.warning("SCHEDULER_JOB_MISSED job=%s", job_id)
        elif event.code == EVENT_JOB_ERROR:
            exception = getattr(event, "exception", None)
            logger.error(
                "SCHEDULER_JOB_FAILED job=%s exception_type=%s",
                job_id,
                exception.__class__.__name__ if exception else "unknown",
                exc_info=exception,
            )
        
    def start(self):
        """Start the live update scheduler"""
        if self.is_running:
            logger.warning("Scheduler is already running")
            return
        
        self.scheduler.add_job(
            self._sync_live_matches_job,
            trigger=IntervalTrigger(seconds=60),
            id="sync_live_matches",
            name="Sync Live Matches",
            max_instances=1  # Prevent overlapping jobs
        )

        self.scheduler.add_job(
            self._recover_league_identities_job,
            trigger=IntervalTrigger(minutes=15),
            id="recover_league_identities",
            name="Recover League Identities",
            max_instances=1,
        )

        # Add Daily Fixtures Sync at 00:01 AM Myanmar Time
        self.scheduler.add_job(
            self._sync_daily_fixtures_job,
            trigger=CronTrigger(hour=0, minute=1, timezone=MM_TZ),
            id="sync_daily_fixtures",
            name="Daily Fixtures Sync",
            max_instances=1
        )

        self.scheduler.add_job(
            self._repair_daily_matches_job,
            trigger=CronTrigger(
                    hour=2,
                    minute=0,
                    timezone=MM_TZ),
            id="repair_daily_matches",
            name="Daily Repair Matches Sync",
            max_instances=1
        )

        self.scheduler.add_job(
            self._refresh_standings_job,
            trigger=IntervalTrigger(hours=6),
            id="refresh_standings",
            name="Refresh Standings",
            max_instances=1,
        )

        self.scheduler.add_job(
            self._refresh_odds_job,
            trigger=IntervalTrigger(minutes=180),
            id="refresh_odds",
            name="Refresh Odds Snapshots",
            max_instances=1,
        )

        self.scheduler.add_job(
            self._refresh_lineups_job,
            trigger=IntervalTrigger(minutes=15),
            id="refresh_lineups",
            name="Refresh Lineups",
            max_instances=1,
        )

        self.scheduler.add_job(
            self._refresh_events_job,
            trigger=IntervalTrigger(seconds=600),
            id="refresh_events",
            name="Refresh Active Match Events",
            max_instances=1,
        )

        self.scheduler.add_job(
            self._refresh_statistics_job,
            trigger=IntervalTrigger(seconds=600),
            id="refresh_statistics",
            name="Refresh Active Match Statistics",
            max_instances=1,
        )

        self.scheduler.add_job(
            self._refresh_h2h_job,
            trigger=IntervalTrigger(hours=6),
            id="refresh_h2h",
            name="Refresh Match H2H",
            max_instances=1,
        )
        
        self.scheduler.start()
        self.is_running = True
        logger.info(
            "SCHEDULER_STARTED jobs=%s",
            list(self.EXPECTED_JOB_IDS),
        )
        
    def stop(self):
        """Stop the live update scheduler"""
        if self.is_running:
            self.scheduler.shutdown(wait=True)
            self.is_running = False
            logger.info("SCHEDULER_STOPPED")

    async def _read_finalization_state(self, match_id: int) -> str | None:
        async with async_session() as verification_db:
            record = await self.match_finalization_repository.get_by_match_id(
                verification_db, match_id
            )
            return record.state if record is not None else None

    async def _invalidate_final_match_caches(self, match_id: int) -> None:
        for cache_key in (
            make_cache_key("live_matches"),
            make_cache_key("match", match_id, "events"),
        ):
            try:
                invalidated = await self.cache_service.delete(cache_key)
                if not invalidated:
                    logger.error(
                        "FINAL_MATCH_SYNC_CACHE_INVALIDATION_FAILED match_id=%s key=%s",
                        match_id,
                        cache_key,
                    )
            except Exception:
                logger.exception(
                    "FINAL_MATCH_SYNC_CACHE_INVALIDATION_FAILED match_id=%s key=%s",
                    match_id,
                    cache_key,
                )
        try:
            await active_match_service.remove_match_active(match_id)
        except Exception:
            logger.exception(
                "FINAL_MATCH_SYNC_ACTIVE_MATCH_INVALIDATION_FAILED match_id=%s",
                match_id,
            )

    async def _finalize_match_sync_candidates(
        self,
        db,
        match_ids: list[int],
        *,
        live_lock_held: bool = False,
    ) -> dict[str, int]:
        candidates = sorted({int(match_id) for match_id in (match_ids or [])})
        metrics = {"attempted": 0, "succeeded": 0, "skipped": 0, "failed": 0}
        if not candidates:
            return metrics

        if not live_lock_held:
            if not await self._acquire_live_match_sync_lock(db):
                logger.info("FINAL_MATCH_SYNC_SKIPPED reason=live_scheduler_lock_not_acquired")
                metrics["skipped"] += len(candidates)
                return metrics

        try:
            for match_id in candidates:
                record = await self.match_finalization_repository.get_by_match_id(
                    db, match_id, for_update=True
                )
                if record is None or record.state in {"SUCCESS", "EXHAUSTED"}:
                    metrics["skipped"] += 1
                    continue

                now = datetime.now(timezone.utc)
                if record.state == "RUNNING":
                    last_attempted_at = record.last_attempted_at
                    stale_before = now - FINALIZATION_RUNNING_STALE_AFTER
                    if (
                        last_attempted_at is not None
                        and last_attempted_at > stale_before
                    ):
                        metrics["skipped"] += 1
                        continue
                    await self.match_finalization_repository.mark_failure(
                        db,
                        record,
                        now=now,
                        category="WORKER_RESTART_RECOVERY",
                        message="Recovered an expired RUNNING finalization attempt.",
                        retryable=True,
                    )
                    await db.commit()
                    metrics["failed"] += 1
                    continue

                if record.state in {"PENDING", "FAILED"} and (
                    record.next_retry_at is not None and record.next_retry_at > now
                ):
                    metrics["skipped"] += 1
                    continue
                if record.state not in {"PENDING", "FAILED"}:
                    metrics["skipped"] += 1
                    continue

                await self.match_finalization_repository.mark_running(db, record, now)
                try:
                    await db.commit()
                except Exception:
                    await db.rollback()
                    persisted_state = await self._read_finalization_state(match_id)
                    if persisted_state != "RUNNING":
                        logger.exception(
                            "FINAL_MATCH_SYNC_ATTEMPT_CLAIM_FAILED match_id=%s",
                            match_id,
                        )
                        metrics["failed"] += 1
                        continue

                metrics["attempted"] += 1

                async def sync_final_state() -> dict:
                    outcome = await football_service.sync_final_match_state(db, match_id)
                    if outcome.get("state") == "SUCCESS":
                        try:
                            await db.commit()
                        except Exception:
                            await db.rollback()
                            persisted_state = await self._read_finalization_state(match_id)
                            if persisted_state == "SUCCESS":
                                outcome["commit_confirmed_by_reread"] = True
                            else:
                                logger.exception(
                                    "FINAL_MATCH_SYNC_COMMIT_UNKNOWN match_id=%s",
                                    match_id,
                                )
                                outcome["state"] = "RUNNING"
                                return outcome
                        return outcome

                    current = await self.match_finalization_repository.get_by_match_id(
                        db, match_id, for_update=True
                    )
                    if current is not None and current.state == "RUNNING":
                        if outcome.get("state") == "WAITING":
                            await self.match_finalization_repository.mark_waiting(
                                db,
                                current,
                                now=datetime.now(timezone.utc),
                            )
                            waiting_state = current.state
                            next_retry_at = current.next_retry_at
                            try:
                                await db.commit()
                            except Exception:
                                await db.rollback()
                                persisted_state = await self._read_finalization_state(
                                    match_id
                                )
                                if persisted_state != waiting_state:
                                    logger.exception(
                                        "FINAL_MATCH_SYNC_WAIT_COMMIT_UNKNOWN match_id=%s",
                                        match_id,
                                    )
                                    return {
                                        **outcome,
                                        "state": "RUNNING",
                                    }
                                outcome["commit_confirmed_by_reread"] = True
                            if waiting_state == "EXHAUSTED":
                                return {
                                    **outcome,
                                    "state": "EXHAUSTED",
                                    "category": "NON_TERMINAL_ATTEMPTS_EXHAUSTED",
                                }
                            return {
                                **outcome,
                                "state": "WAITING",
                                "next_retry_at": next_retry_at,
                            }

                        await self.match_finalization_repository.mark_failure(
                            db,
                            current,
                            now=datetime.now(timezone.utc),
                            category=outcome.get("category", "FINAL_MATCH_SYNC_FAILURE"),
                            message=outcome.get("message", "Final Match verification failed."),
                            retryable=bool(outcome.get("retryable")),
                        )
                        await db.commit()
                    return outcome

                try:
                    resource_locked, outcome = await run_with_resource_lock(
                        db,
                        "live_sync",
                        "global",
                        sync_final_state,
                    )
                except Exception:
                    await db.rollback()
                    logger.exception("FINAL_MATCH_SYNC_ATTEMPT_FAILED match_id=%s", match_id)
                    metrics["failed"] += 1
                    continue

                if not resource_locked:
                    await db.rollback()
                    current = await self.match_finalization_repository.get_by_match_id(
                        db, match_id, for_update=True
                    )
                    if current is not None and current.state == "RUNNING":
                        await self.match_finalization_repository.mark_failure(
                            db,
                            current,
                            now=datetime.now(timezone.utc),
                            category="LOCK_CONFLICT",
                            message="LIVE_SYNC resource lock was unavailable.",
                            retryable=True,
                        )
                        await db.commit()
                    metrics["skipped"] += 1
                    continue

                if isinstance(outcome, dict) and outcome.get("state") == "SUCCESS":
                    metrics["succeeded"] += 1
                    await self._invalidate_final_match_caches(match_id)
                elif isinstance(outcome, dict) and outcome.get("state") == "EXHAUSTED":
                    metrics["failed"] += 1
                elif isinstance(outcome, dict) and outcome.get("state") == "FAILED":
                    metrics["failed"] += 1
                else:
                    metrics["skipped"] += 1
        finally:
            if not live_lock_held:
                await self._release_live_match_sync_lock(db)

        return metrics
            
    async def _should_sync_live_matches(self, db) -> bool:
        now = datetime.now(timezone.utc)
        post_kickoff_window = timedelta(
            minutes=settings.get_post_kickoff_max_window_minutes()
        )

        candidate_result = await db.execute(
            select(Match.status, Match.match_time)
            .where(Match.provider == API_FOOTBALL_PROVIDER)
        )
        candidates = candidate_result.all()

        eligible_matches = 0
        for status, match_time in candidates:
            normalized_status = str(status or "").strip().upper()
            if normalized_status in LIVE_STATUSES:
                eligible_matches += 1
                continue
            if normalized_status in LIVE_SYNC_STOP_STATUSES or normalized_status != NS_STATUS:
                continue

            if match_time.tzinfo is None or match_time.utcoffset() is None:
                raise ValueError(
                    "LIVE_SYNC Gate requires timezone-aware Match.match_time values."
                )
            kickoff_utc = match_time.astimezone(timezone.utc)
            if (
                now >= kickoff_utc - PRE_LIVE_WINDOW
                and now < kickoff_utc + post_kickoff_window
            ):
                eligible_matches += 1

        should_sync = eligible_matches > 0
        logger.debug(
            "LIVE_SYNC_RUN_DECISION_GATE",
            extra={
                "provider": API_FOOTBALL_PROVIDER,
                "candidate_match_count": len(candidates),
                "eligible_match_count": eligible_matches,
                "now_utc": now.isoformat(),
                "pre_live_window_minutes": PRE_LIVE_WINDOW.total_seconds() / 60,
                "post_kickoff_max_window_minutes": post_kickoff_window.total_seconds() / 60,
                "should_sync": should_sync,
            },
        )
        return should_sync

    async def _sync_live_matches_job(self):
        """Job function to sync live matches"""
        try:
            async with async_session() as db:
                lock_acquired = await self._acquire_live_match_sync_lock(db)
                if not lock_acquired:
                    logger.info("LIVE_SYNC_SKIPPED reason=lock_not_acquired")
                    return

                try:
                    due_finalizations = (
                        await self.match_finalization_repository.get_due_match_ids(
                            db, datetime.now(timezone.utc)
                        )
                        if hasattr(db, "info")
                        else []
                    )
                    await self._finalize_match_sync_candidates(
                        db,
                        due_finalizations,
                        live_lock_held=True,
                    )

                    if not await self._should_sync_live_matches(db):
                        logger.debug("No near-start or active non-FT matches found; skipping live sync")
                        return

                    async def sync_live() -> dict:
                        try:
                            result = await football_service.sync_live_matches(db)
                            if result.get("success"):
                                await db.commit()
                                active_match_updates = result.get("active_match_updates") or {}
                                if active_match_updates:
                                    try:
                                        await football_service.apply_active_match_updates(active_match_updates)
                                    except Exception:
                                        logger.exception(
                                            "LIVE_SYNC_ACTIVE_MATCH_REGISTRATION_FAILED updates=%s",
                                            active_match_updates,
                                        )
                                try:
                                    cache_invalidated = await self.cache_service.delete(
                                        make_cache_key("live_matches")
                                    )
                                    if not cache_invalidated:
                                        logger.warning("LIVE_SYNC_CACHE_INVALIDATION_FAILED key=live_matches")
                                except Exception:
                                    logger.exception("LIVE_SYNC_CACHE_INVALIDATION_FAILED key=live_matches")
                            else:
                                await db.rollback()
                            return result
                        except Exception:
                            await db.rollback()
                            raise

                    resource_locked, result = await run_with_resource_lock(
                        db, "live_sync", "global", sync_live
                    )
                    if not resource_locked:
                        logger.info("LIVE_SYNC_SKIPPED reason=resource_lock_not_acquired")
                        return
                    await self._finalize_match_sync_candidates(
                        db,
                        result.get("final_match_sync_candidates", []),
                        live_lock_held=True,
                    )
                    SCHEDULER_JOB_RUNS.labels(job="sync_live_matches").inc()
                    if result.get("success"):
                        if result.get("updated", 0) > 0:
                            logger.info(f"Live sync completed: {result}")
                    else:
                        logger.error(f"Live sync failed: {result}")
                finally:
                    await self._release_live_match_sync_lock(db)
        except Exception as e:
            SCHEDULER_JOB_ERRORS.labels(job="sync_live_matches").inc()
            logger.error(f"Error in live sync job: {e}")
            # Continue running even if one job fails

    async def _recover_league_identities_job(self):
        now = datetime.now(timezone.utc)
        metrics = {"selected": 0, "resolved": 0, "retryable": 0, "failed": 0}
        repository = LeagueIdentityRecoveryRepository()
        try:
            async with async_session() as db:
                candidates = await repository.get_retry_candidates(db, now, limit=100)
                metrics["selected"] = len(candidates)
            for candidate in candidates:
                try:
                    result = await football_service.league_service.recover_provider_identity(
                        int(candidate.league_id)
                    )
                    if result.get("success"):
                        metrics["resolved"] += 1
                    elif result.get("retryable"):
                        metrics["retryable"] += 1
                    else:
                        metrics["failed"] += 1
                except Exception:
                    metrics["failed"] += 1
                    logger.exception(
                        "LEAGUE_IDENTITY_RECOVERY_JOB_FAILED league_id=%s",
                        candidate.league_id,
                    )
            logger.info("LEAGUE_IDENTITY_RECOVERY_JOB_COMPLETE metrics=%s", metrics)
            return metrics
        except Exception:
            SCHEDULER_JOB_ERRORS.labels(job="recover_league_identities").inc()
            logger.exception("LEAGUE_IDENTITY_RECOVERY_JOB_FAILED metrics=%s", metrics)
            return metrics

    async def _sync_daily_fixtures_job(self):
        """Job function to sync all fixtures for the current day"""
        try:
            # Get today's date in Myanmar timezone (YYYY-MM-DD)
            today = datetime.now(MM_TZ).strftime("%Y-%m-%d")
            async with async_session() as db:
                lock_acquired = await self._acquire_daily_fixture_sync_lock(db)
                if not lock_acquired:
                    logger.info("DAILY_FIXTURE_SYNC_SKIPPED reason=lock_not_acquired")
                    return

                try:
                    logger.info(f"Starting automatic daily sync for {today}")
                    async def sync_daily() -> dict:
                        try:
                            result = await football_service.sync_daily_fixtures(db, today)
                            if result.get("success"):
                                await db.commit()
                                await football_service.apply_active_match_updates(result.get("active_match_updates"))
                                try:
                                    await self.cache_service.delete(make_cache_key("live_matches"))
                                except Exception:
                                    logger.exception("DAILY_FIXTURE_SYNC_CACHE_INVALIDATION_FAILED")
                                if "final_lineup_candidates" in result:
                                    await football_service.finalize_pending_lineups(
                                        result.get("final_lineup_candidates", [])
                                    )
                            else:
                                await db.rollback()
                            return result
                        except Exception:
                            await db.rollback()
                            raise

                    resource_locked, result = await run_with_resource_lock(
                        db, "fixture_query", "global", sync_daily
                    )
                    if not resource_locked:
                        logger.info("DAILY_FIXTURE_SYNC_SKIPPED reason=resource_lock_not_acquired")
                        return
                    SCHEDULER_JOB_RUNS.labels(job="sync_daily_fixtures").inc()
                    logger.info(f"Automatic daily sync completed: {result}")
                finally:
                    await self._release_daily_fixture_sync_lock(db)
        except Exception as e:
            SCHEDULER_JOB_ERRORS.labels(job="sync_daily_fixtures").inc()
            logger.error(f"Error in daily sync job: {e}")

    async def _repair_daily_matches_job(self):
        """Job function to repair live/stuck matches by re-syncing yesterday and today."""
        try:
            today = datetime.now().date()
            yesterday = today - timedelta(days=1)
            yesterday_str = yesterday.strftime("%Y-%m-%d")
            today_str = today.strftime("%Y-%m-%d")

            async with async_session() as db:
                lock_acquired = await self._acquire_repair_daily_matches_lock(db)
                if not lock_acquired:
                    logger.info("REPAIR_DAILY_MATCHES_SKIPPED reason=lock_not_acquired")
                    return

                try:
                    logger.info(f"Starting daily repair sync for {yesterday_str} and {today_str}")

                    async def sync_repair() -> tuple[dict, dict]:
                        try:
                            result_yesterday = await football_service.sync_daily_fixtures(db, yesterday_str)
                            if result_yesterday.get("success"):
                                await db.commit()
                                await football_service.apply_active_match_updates(result_yesterday.get("active_match_updates"))
                                try:
                                    await self.cache_service.delete(make_cache_key("live_matches"))
                                except Exception:
                                    logger.exception("REPAIR_CACHE_INVALIDATION_FAILED target=%s", yesterday_str)
                                if "final_lineup_candidates" in result_yesterday:
                                    await football_service.finalize_pending_lineups(
                                        result_yesterday.get("final_lineup_candidates", [])
                                    )
                            else:
                                await db.rollback()
                            logger.info(f"Daily repair sync for {yesterday_str} completed: {result_yesterday}")

                            result_today = await football_service.sync_daily_fixtures(db, today_str)
                            if result_today.get("success"):
                                await db.commit()
                                await football_service.apply_active_match_updates(result_today.get("active_match_updates"))
                                try:
                                    await self.cache_service.delete(make_cache_key("live_matches"))
                                except Exception:
                                    logger.exception("REPAIR_CACHE_INVALIDATION_FAILED target=%s", today_str)
                                if "final_lineup_candidates" in result_today:
                                    await football_service.finalize_pending_lineups(
                                        result_today.get("final_lineup_candidates", [])
                                    )
                            logger.info(f"Daily repair sync for {today_str} completed: {result_today}")
                            return result_yesterday, result_today
                        except Exception:
                            await db.rollback()
                            raise

                    resource_locked, _ = await run_with_resource_lock(
                        db, "fixture_query", "global", sync_repair
                    )
                    if not resource_locked:
                        logger.info("REPAIR_DAILY_MATCHES_SKIPPED reason=resource_lock_not_acquired")
                        return

                    SCHEDULER_JOB_RUNS.labels(job="repair_daily_matches").inc()
                finally:
                    await self._release_repair_daily_matches_lock(db)
        except Exception as e:
            SCHEDULER_JOB_ERRORS.labels(job="repair_daily_matches").inc()
            logger.error(f"Error in daily repair sync job: {e}")

    async def _refresh_odds_job(self):
        metrics = {
            "eligible_matches": 0,
            "processed_matches": 0,
            "refreshed_matches": 0,
            "skipped_matches": 0,
            "failed_matches": 0,
            "blocked_matches": 0,
            "cache_failures": 0,
            "retry_state_failures": 0,
        }

        try:
            now_utc = datetime.now(timezone.utc)
            window_end = now_utc + timedelta(hours=ODDS_REFRESH_WINDOW_HOURS)
            async with async_session() as db:
                refreshed_cache_keys = []
                committed_projections = []
                reconciliation_complete = await reconcile_pending_odds_attempts(
                    db,
                    self.cache_service,
                    football_service.odds_sync_service,
                    self.pending_odds_cache_invalidations,
                    metrics,
                )
                if not reconciliation_complete:
                    logger.error("ODDS_REFRESH_ABORTED reason=reconciliation_state_unavailable")
                    return metrics
                recovery_complete = await recover_pending_odds_cache(
                    db,
                    self.cache_service,
                    football_service.odds_sync_service,
                    self.pending_odds_cache_invalidations,
                    metrics,
                )
                if not recovery_complete:
                    logger.error("ODDS_REFRESH_ABORTED reason=retry_state_recovery_unavailable")
                    return metrics
                result = await db.execute(
                    select(Match.match_id, Match.provider_fixture_id, Match.status, Match.match_time)
                    .where(Match.status.in_(ODDS_REFRESH_ELIGIBLE_STATUSES))
                    .where(Match.match_time >= now_utc)
                    .where(Match.match_time <= window_end)
                    .order_by(Match.match_time.asc(), Match.match_id.asc())
                )
                eligible_matches = result.all()

                for match_id, provider_fixture_id, status, match_time in eligible_matches:
                    status_upper = str(status or "").upper()
                    if status_upper in ODDS_REFRESH_STOP_STATUSES:
                        metrics["skipped_matches"] += 1
                        continue

                    if match_time is not None and match_time < now_utc:
                        metrics["skipped_matches"] += 1
                        continue

                    if match_time is not None and match_time > window_end:
                        metrics["skipped_matches"] += 1
                        continue

                    metrics["eligible_matches"] += 1
                    metrics["processed_matches"] += 1
                    async def refresh_fixture() -> dict:
                        cache_key = make_cache_key("match", match_id, "odds")
                        odds_sync_service = football_service.odds_sync_service
                        eligibility = await odds_sync_service.check_retry_eligibility(db, match_id)

                        if eligibility.get("reason") == COMMIT_UNKNOWN:
                            await db.rollback()
                            return {
                                "state": "skipped",
                                "status": RETRY_BLOCKED,
                                "reason": COMMIT_UNKNOWN,
                            }

                        if eligibility.get("cache_invalidation_pending"):
                            try:
                                recovered = await self.cache_service.delete(cache_key)
                            except Exception:
                                recovered = False
                            if not recovered:
                                await db.rollback()
                                self.pending_odds_cache_invalidations.add(match_id)
                                logger.warning(
                                    "ODDS_CACHE_RECOVERY",
                                    extra={"local_match_id": match_id, "state": CACHE_INVALIDATION_FAILED},
                                )
                                return {
                                    "state": "failed",
                                    "status": CACHE_INVALIDATION_FAILED,
                                    "reason": "CACHE_RECOVERY_FAILED",
                                }
                            try:
                                await odds_sync_service.retry_state_repository.clear_cache_invalidation_pending(
                                    db,
                                    match_id,
                                )
                                await db.commit()
                                self.pending_odds_cache_invalidations.discard(match_id)
                                logger.info(
                                    "ODDS_CACHE_RECOVERY",
                                    extra={"local_match_id": match_id, "state": "CACHE_INVALIDATED"},
                                )
                                return {
                                    "state": "skipped",
                                    "status": "CACHE_RECOVERED",
                                    "reason": "CACHE_RECOVERY_COMPLETED",
                                }
                            except Exception as exc:
                                await db.rollback()
                                odds_sync_service.mark_retry_state_unavailable(match_id, exc)
                                self.pending_odds_cache_invalidations.add(match_id)
                                return {
                                    "state": "failed",
                                    "status": RETRY_STATE_UNAVAILABLE,
                                    "reason": "CACHE_RECOVERY_STATE_CLEAR_FAILED",
                                }

                        if not eligibility.get("eligible"):
                            await db.rollback()
                            return {
                                "state": "skipped",
                                "status": eligibility.get("status", RETRY_BLOCKED),
                                "reason": eligibility.get("reason", "RETRY_BLOCKED"),
                            }

                        snapshot_rows = await football_service.odds_sync_service.odds_repository.get_fixture_odds(db, match_id)
                        latest_update = max((row.last_updated for row in snapshot_rows if row.last_updated), default=None)
                        valid_snapshot = football_service.odds_service.is_valid_snapshot(snapshot_rows)

                        retry_due = bool(eligibility.get("retry_due"))
                        if (
                            not retry_due
                            and valid_snapshot
                            and latest_update
                            and (now_utc - latest_update) < ODDS_REFRESH_MAX_AGE
                        ):
                            await db.rollback()
                            return {"state": "skipped"}

                        try:
                            async with async_session() as attempt_db:
                                attempt_id = await odds_sync_service.prepare_refresh_attempt(
                                    attempt_db,
                                    match_id,
                                    provider_fixture_id,
                                )
                                await attempt_db.commit()
                        except OddsIdentityLookupError as exc:
                            logger.exception(
                                "ODDS_MATCH_IDENTITY_LOOKUP_FAILED",
                                extra={
                                    "local_match_id": match_id,
                                    "failure_type": exc.__class__.__name__,
                                },
                            )
                            result = {
                                "state": "failed",
                                "status": IDENTITY_UNAVAILABLE,
                                "reason": "MATCH_IDENTITY_LOOKUP_FAILED",
                            }
                            try:
                                await db.rollback()
                            except Exception as rollback_exc:
                                logger.exception(
                                    "ODDS_IDENTITY_FAILURE_ROLLBACK_FAILED",
                                    extra={
                                        "local_match_id": match_id,
                                        "identity_failure_type": exc.__class__.__name__,
                                        "rollback_failure_type": rollback_exc.__class__.__name__,
                                    },
                                )
                                result["outer_rollback_failed"] = True
                            return result
                        except OddsMatchIdentityError as exc:
                            logger.warning(
                                "ODDS_MATCH_IDENTITY_REJECTED",
                                extra={
                                    "local_match_id": match_id,
                                    "provider_fixture_id": provider_fixture_id,
                                    "reason": exc.reason,
                                },
                            )
                            result = {
                                "state": "failed",
                                "status": IDENTITY_REJECTED,
                                "reason": exc.reason,
                            }
                            try:
                                await db.rollback()
                            except Exception as rollback_exc:
                                logger.exception(
                                    "ODDS_IDENTITY_FAILURE_ROLLBACK_FAILED",
                                    extra={
                                        "local_match_id": match_id,
                                        "identity_failure_type": exc.__class__.__name__,
                                        "rollback_failure_type": rollback_exc.__class__.__name__,
                                    },
                                )
                                result["outer_rollback_failed"] = True
                            return result
                        except Exception as exc:
                            logger.exception(
                                "ODDS_ATTEMPT_EVIDENCE_WRITE_FAILED",
                                extra={"local_match_id": match_id, "failure_type": exc.__class__.__name__},
                            )
                            odds_sync_service.mark_retry_state_unavailable(match_id, exc)
                            return {
                                "state": "failed",
                                "status": RETRY_STATE_UNAVAILABLE,
                                "reason": "ATTEMPT_EVIDENCE_WRITE_FAILED",
                            }

                        refresh_result = await odds_sync_service.refresh_odds(
                            db,
                            provider_fixture_id,
                            cache_key,
                            1800,
                            local_match_id=match_id,
                            attempt_id=attempt_id,
                        )
                        if refresh_result.get("status") != CANDIDATE_READY:
                            await db.rollback()
                            try:
                                async with async_session() as attempt_db:
                                    await odds_sync_service.resolve_attempt_without_candidate(
                                        attempt_db,
                                        match_id,
                                        attempt_id,
                                        str(refresh_result.get("status", "REFRESH_FAILED")),
                                    )
                                    await attempt_db.commit()
                            except Exception as exc:
                                logger.exception(
                                    "ODDS_ATTEMPT_RESOLUTION_FAILED",
                                    extra={"local_match_id": match_id, "failure_type": exc.__class__.__name__},
                                )
                                odds_sync_service.mark_retry_state_unavailable(match_id, exc)
                                return {
                                    "state": "failed",
                                    "status": RETRY_STATE_UNAVAILABLE,
                                    "reason": "ATTEMPT_RESOLUTION_FAILED",
                                }
                            return {
                                "state": "failed",
                                "status": refresh_result.get("status"),
                                "reason": refresh_result.get("reason", refresh_result.get("status")),
                                "outcome": refresh_result.get("status"),
                            }

                        try:
                            candidate_timestamp = refresh_result["_transaction_context"]["persistence_rows"][0]["last_updated"]
                            await odds_sync_service.retry_state_repository.mark_success_pending_cache(
                                db,
                                match_id,
                                candidate_timestamp,
                            )
                        except Exception as exc:
                            await db.rollback()
                            odds_sync_service.mark_retry_state_unavailable(match_id, exc)
                            return {
                                "state": "failed",
                                "status": RETRY_STATE_UNAVAILABLE,
                                "reason": "RETRY_STATE_SUCCESS_WRITE_FAILED",
                            }

                        commit_result = await commit_odds_refresh(
                            db,
                            odds_sync_service,
                            match_id,
                            refresh_result,
                        )
                        if commit_result["state"] != "refreshed":
                            return commit_result
                        commit_result["cache_state"] = await invalidate_odds_cache_after_commit(
                            self.cache_service,
                            self.pending_odds_cache_invalidations,
                            match_id,
                            cache_key,
                        )
                        if commit_result["cache_state"] == "CACHE_INVALIDATED":
                            try:
                                async def clear_pending() -> None:
                                    await odds_sync_service.retry_state_repository.clear_cache_invalidation_pending(
                                        db,
                                        match_id,
                                    )
                                    await db.commit()

                                pending_cleared, _ = await run_with_resource_lock(
                                    db,
                                    "odds",
                                    match_id,
                                    clear_pending,
                                )
                                if not pending_cleared:
                                    raise RuntimeError("Odds cache-pending state lock unavailable")
                            except Exception as exc:
                                await db.rollback()
                                odds_sync_service.mark_retry_state_unavailable(match_id, exc)
                                self.pending_odds_cache_invalidations.add(match_id)
                                commit_result["cache_state"] = "CACHE_RECOVERY_PENDING"
                        return commit_result

                    try:
                        resource_locked, refresh_result = await run_with_resource_lock(
                            db,
                            "odds",
                            match_id,
                            refresh_fixture,
                        )
                        if not resource_locked:
                            await db.rollback()
                            metrics["skipped_matches"] += 1
                            logger.info("ODDS_REFRESH_SKIPPED match_id=%s reason=resource_lock_not_acquired", match_id)
                            continue

                        state = refresh_result.get("state")
                        if state == "skipped":
                            metrics["skipped_matches"] += 1
                            if refresh_result.get("status") == RETRY_STATE_UNAVAILABLE:
                                metrics["retry_state_failures"] += 1
                                logger.error(
                                    "ODDS_RETRY_STATE_UNAVAILABLE",
                                    extra={"local_match_id": match_id, "reason": refresh_result.get("reason")},
                                )
                            elif refresh_result.get("reason") == COMMIT_UNKNOWN:
                                metrics["blocked_matches"] += 1
                        elif state != "refreshed":
                            reason = refresh_result.get("reason")
                            outcome = refresh_result.get("status", refresh_result.get("outcome"))
                            if outcome in {
                                NO_DATA,
                                VALIDATION_FAILED,
                                PROVIDER_FAILED,
                                PERSISTENCE_FAILED,
                                COMMIT_NOT_APPLIED,
                                COMMIT_UNKNOWN,
                            }:
                                persisted = await persist_odds_retry_failure(
                                    db,
                                    football_service.odds_sync_service,
                                    match_id,
                                    refresh_result,
                                )
                                if not persisted:
                                    metrics["retry_state_failures"] += 1
                            elif outcome == RETRY_STATE_UNAVAILABLE:
                                metrics["retry_state_failures"] += 1
                            elif outcome == CACHE_INVALIDATION_FAILED:
                                metrics["cache_failures"] += 1
                            metrics["failed_matches"] += 1
                            logger.error("ODDS_REFRESH_FAILED match_id=%s outcome=%s reason=%s", match_id, refresh_result.get("outcome"), reason)
                        else:
                            committed_projection = refresh_result["result"].get("analytics")
                            if committed_projection:
                                log_projection_transaction(committed_projection, "committed")
                            metrics["refreshed_matches"] += 1
                            if refresh_result.get("cache_state") != "CACHE_INVALIDATED":
                                metrics["cache_failures"] += 1
                            logger.debug("ODDS_REFRESH_SYNCED match_id=%s", match_id)
                        if refresh_result.get("outer_rollback_failed"):
                            break
                    except Exception:
                        await db.rollback()
                        metrics["failed_matches"] += 1
                        logger.exception("ODDS_REFRESH_FAILED match_id=%s", match_id)

                SCHEDULER_JOB_RUNS.labels(job="refresh_odds").inc()
                logger.info("ODDS_REFRESH_COMPLETE metrics=%s", metrics)
                return metrics
        except Exception:
            SCHEDULER_JOB_ERRORS.labels(job="refresh_odds").inc()
            logger.exception("Error in odds refresh job")
            return metrics

    async def _get_allowed_standings_pairs(self, db) -> list[tuple[int, int]]:
        result = await db.execute(
            select(LeagueSeason.league_id, LeagueSeason.season)
            .join(AllowedLeague, AllowedLeague.league_id == LeagueSeason.league_id)
            .order_by(LeagueSeason.league_id.asc(), LeagueSeason.season.asc())
        )
        pairs = set()
        for league_id, season in result.all():
            try:
                season_value = int(normalize_season(season))
            except ValueError:
                logger.warning(
                    "STANDINGS_REFRESH_SKIPPED league_id=%s season=%s reason=invalid_league_season",
                    league_id,
                    season,
                )
                continue
            pairs.add((int(league_id), season_value))
        return sorted(pairs)

    async def _refresh_standings_job(self):
        metrics = {
            "processed_pairs": 0,
            "success_pairs": 0,
            "failed_pairs": 0,
        }

        try:
            async with async_session() as db:
                lock_acquired = await self._acquire_standings_refresh_lock(db)
                if not lock_acquired:
                    logger.info("STANDINGS_REFRESH_SKIPPED reason=lock_not_acquired")
                    return metrics

                try:
                    pairs = await self._get_allowed_standings_pairs(db)
                    logger.info("STANDINGS_REFRESH_START total_pairs=%s metrics=%s", len(pairs), metrics)

                    for league_id, season in pairs:
                        metrics["processed_pairs"] += 1
                        logger.info("STANDINGS_REFRESH_LEAGUE league_id=%s season=%s", league_id, season)

                        try:
                            async def sync_standing() -> dict:
                                result = await football_service.sync_standings(db, league_id, season)
                                if result.get("success"):
                                    await db.commit()
                                    if result.get("analytics"):
                                        log_projection_transaction(result["analytics"], "committed")
                                return result

                            resource_locked, result = await run_with_resource_lock(
                                db,
                                "standing",
                                f"{league_id}:{season}",
                                sync_standing,
                            )
                            if not resource_locked:
                                metrics["failed_pairs"] += 1
                                logger.info(
                                    "STANDINGS_REFRESH_SKIPPED league_id=%s season=%s reason=resource_lock_not_acquired",
                                    league_id,
                                    season,
                                )
                                continue

                            if result.get("success"):
                                metrics["success_pairs"] += 1
                                logger.info(
                                    "STANDINGS_REFRESH_SUCCESS league_id=%s season=%s updated=%s",
                                    league_id,
                                    season,
                                    result.get("updated", 0),
                                )
                                continue

                            await db.rollback()
                            metrics["failed_pairs"] += 1
                            logger.error(
                                "STANDINGS_REFRESH_FAILED league_id=%s season=%s result=%s",
                                league_id,
                                season,
                                result,
                            )
                        except Exception:
                            await db.rollback()
                            metrics["failed_pairs"] += 1
                            logger.exception("STANDINGS_REFRESH_FAILED league_id=%s season=%s", league_id, season)

                    SCHEDULER_JOB_RUNS.labels(job="refresh_standings").inc()
                    logger.info("STANDINGS_REFRESH_COMPLETE metrics=%s", metrics)
                    return metrics
                finally:
                    await self._release_standings_refresh_lock(db)
        except Exception:
            SCHEDULER_JOB_ERRORS.labels(job="refresh_standings").inc()
            logger.exception("Error in standings refresh job")
            return metrics

    async def _acquire_advisory_lock(self, db, lock_key: int) -> bool:
        """Acquire a session lock on a connection pinned for the job lifetime."""
        if not hasattr(db, "execute"):
            return True
        connection = None
        try:
            connection = await engine.connect()
            result = await connection.execute(
                text("SELECT pg_try_advisory_lock(:lock_key)"),
                {"lock_key": lock_key},
            )
            if not bool(result.scalar_one()):
                await connection.close()
                return False

            await connection.commit()
            lock_connections = db.info.setdefault(_SCHEDULER_ADVISORY_LOCK_CONNECTIONS, {})
            lock_connections[lock_key] = connection
            logger.info("SCHEDULER_ADVISORY_LOCK_ACQUIRED lock_key=%s", lock_key)
            return True
        except Exception:
            logger.exception("SCHEDULER_ADVISORY_LOCK_ACQUIRE_FAILED lock_key=%s", lock_key)
            if connection is not None:
                try:
                    await connection.invalidate()
                except Exception:
                    logger.exception("SCHEDULER_ADVISORY_LOCK_INVALIDATE_FAILED lock_key=%s", lock_key)
            raise

    async def _release_advisory_lock(self, db, lock_key: int) -> None:
        """Verify unlock on the same pinned connection that acquired the lock."""
        if not hasattr(db, "execute"):
            return
        lock_connections = db.info.get(_SCHEDULER_ADVISORY_LOCK_CONNECTIONS, {})
        connection = lock_connections.pop(lock_key, None)
        if connection is None:
            raise RuntimeError(f"Scheduler advisory lock connection missing for lock_key={lock_key}")

        try:
            result = await connection.execute(
                text("SELECT pg_advisory_unlock(:lock_key)"),
                {"lock_key": lock_key},
            )
            if not bool(result.scalar_one()):
                raise RuntimeError(f"PostgreSQL did not release advisory lock lock_key={lock_key}")
            await connection.commit()
            logger.info("SCHEDULER_ADVISORY_LOCK_RELEASED lock_key=%s", lock_key)
        except Exception:
            logger.exception("SCHEDULER_ADVISORY_LOCK_RELEASE_FAILED lock_key=%s", lock_key)
            try:
                await connection.invalidate()
            except Exception:
                logger.exception("SCHEDULER_ADVISORY_LOCK_INVALIDATE_FAILED lock_key=%s", lock_key)
            raise
        finally:
            try:
                await connection.close()
            except Exception:
                logger.exception("SCHEDULER_ADVISORY_LOCK_CONNECTION_CLOSE_FAILED lock_key=%s", lock_key)
                try:
                    await connection.invalidate()
                except Exception:
                    logger.exception("SCHEDULER_ADVISORY_LOCK_INVALIDATE_FAILED lock_key=%s", lock_key)
                raise

    async def _acquire_live_match_sync_lock(self, db) -> bool:
        return await self._acquire_advisory_lock(db, LIVE_MATCH_SYNC_LOCK_KEY)

    async def _release_live_match_sync_lock(self, db) -> None:
        await self._release_advisory_lock(db, LIVE_MATCH_SYNC_LOCK_KEY)

    async def _acquire_daily_fixture_sync_lock(self, db) -> bool:
        return await self._acquire_advisory_lock(db, DAILY_FIXTURE_SYNC_LOCK_KEY)

    async def _release_daily_fixture_sync_lock(self, db) -> None:
        await self._release_advisory_lock(db, DAILY_FIXTURE_SYNC_LOCK_KEY)

    async def _acquire_repair_daily_matches_lock(self, db) -> bool:
        return await self._acquire_advisory_lock(db, REPAIR_DAILY_MATCHES_LOCK_KEY)

    async def _release_repair_daily_matches_lock(self, db) -> None:
        await self._release_advisory_lock(db, REPAIR_DAILY_MATCHES_LOCK_KEY)

    async def _acquire_standings_refresh_lock(self, db) -> bool:
        """Acquire a PostgreSQL advisory lock to avoid cross-instance duplicate standings refresh runs."""
        return await self._acquire_advisory_lock(db, STANDINGS_REFRESH_LOCK_KEY)

    async def _release_standings_refresh_lock(self, db) -> None:
        """Release the PostgreSQL advisory lock acquired for standings refresh."""
        await self._release_advisory_lock(db, STANDINGS_REFRESH_LOCK_KEY)

    async def _get_lineup_refresh_candidates(
        self,
        db,
        now_utc: datetime | None = None,
        window_minutes: int = 90,
    ) -> list[int]:
        now_utc = now_utc or datetime.now(timezone.utc)
        window_end = now_utc + timedelta(minutes=window_minutes)
        result = await db.execute(
            select(Match.match_id)
            .join(AllowedLeague, AllowedLeague.league_id == Match.league_id)
            .where(Match.season.is_not(None))
            .where(Match.status == "NS")
            .where(Match.match_time > now_utc)
            .where(Match.match_time <= window_end)
            .order_by(Match.match_time.asc())
        )
        return [int(row[0]) for row in result.all()]

    async def _refresh_lineups_job(self):
        metrics = {
            "candidate_matches": 0,
            "processed_matches": 0,
            "synced_matches": 0,
            "partial_matches": 0,
            "skipped_matches": 0,
            "failed_matches": 0,
        }

        now_utc = datetime.now(timezone.utc)
        cooldown_seconds = settings.LINEUP_REFRESH_COOLDOWN_SECONDS

        try:
            async with async_session() as db:
                candidates = await self._get_lineup_refresh_candidates(db, now_utc=now_utc, window_minutes=90)
                metrics["candidate_matches"] = len(candidates)

                logger.info("LINEUP_REFRESH_START total_candidates=%s metrics=%s", len(candidates), metrics)

                for match_id in candidates:
                    metrics["processed_matches"] += 1
                    logger.debug("LINEUP_REFRESH_MATCH match_id=%s", match_id)

                    try:
                        on_cooldown = await self.lineup_refresh_state_repository.is_on_cooldown(
                            db,
                            match_id,
                            cooldown_seconds=cooldown_seconds,
                            now_utc=now_utc,
                        )
                        if on_cooldown:
                            metrics["skipped_matches"] += 1
                            logger.info("LINEUP_REFRESH_SKIPPED match_id=%s reason=cooldown", match_id)
                            continue

                        async def sync_lineup() -> dict:
                            result = await football_service.sync_match_lineup(db, match_id)
                            if result.get("success"):
                                await self.lineup_refresh_state_repository.touch(db, match_id, refreshed_at=now_utc)
                                await db.commit()
                                if result.get("analytics"):
                                    log_projection_transaction(result["analytics"], "committed")
                                if not result.get("skipped"):
                                    try:
                                        await self.cache_service.delete(make_cache_key("lineup", match_id))
                                    except Exception:
                                        logger.exception("LINEUP_REFRESH_CACHE_INVALIDATION_FAILED match_id=%s", match_id)
                            return result

                        resource_locked, result = await run_with_resource_lock(
                            db,
                            "lineup",
                            match_id,
                            sync_lineup,
                        )
                        if not resource_locked:
                            metrics["skipped_matches"] += 1
                            logger.info("LINEUP_REFRESH_SKIPPED match_id=%s reason=resource_lock_not_acquired", match_id)
                            continue

                        if result.get("success") and not result.get("skipped"):
                            if result.get("partial"):
                                metrics["partial_matches"] += 1
                                logger.info("LINEUP_REFRESH_PARTIAL_SYNCED match_id=%s", match_id)
                            else:
                                metrics["synced_matches"] += 1
                                logger.info("LINEUP_REFRESH_SYNCED match_id=%s", match_id)
                        elif result.get("success"):
                            metrics["skipped_matches"] += 1
                            logger.info(
                                "LINEUP_REFRESH_SKIPPED match_id=%s reason=%s status=%s",
                                match_id,
                                result.get("reason"),
                                result.get("status"),
                            )
                        else:
                            await db.rollback()
                            metrics["failed_matches"] += 1
                            logger.error("LINEUP_REFRESH_FAILED match_id=%s reason=%s", match_id, result.get("reason"))
                        continue

                        await db.rollback()
                        metrics["failed_matches"] += 1
                        logger.error(
                            "LINEUP_REFRESH_FAILED match_id=%s reason=%s",
                            match_id,
                            result.get("reason", "lineup_sync_failed"),
                        )
                    except Exception:
                        await db.rollback()
                        metrics["failed_matches"] += 1
                        logger.exception("LINEUP_REFRESH_FAILED match_id=%s", match_id)

                SCHEDULER_JOB_RUNS.labels(job="refresh_lineups").inc()
                logger.info("LINEUP_REFRESH_COMPLETE metrics=%s", metrics)
                return metrics
        except Exception:
            SCHEDULER_JOB_ERRORS.labels(job="refresh_lineups").inc()
            logger.exception("Error in lineup refresh job")
            return metrics

    async def _get_match_status_for_event_refresh(self, db, match_id: int) -> str | None:
        result = await db.execute(select(Match.status).where(Match.match_id == match_id))
        status = result.scalar_one_or_none()
        return str(status).upper() if status else None

    async def _get_last_event_sync_at(self, db, match_id: int) -> datetime | None:
        try:
            result = await db.execute(
                select(MatchEvent.updated_at)
                .where(MatchEvent.match_id == match_id)
                .order_by(MatchEvent.updated_at.desc())
                .limit(1)
            )
        except Exception:
            logger.warning("EVENT_REFRESH_SYNC_TIME_LOOKUP_FAILED match_id=%s", match_id)
            return None

        last_sync_at = result.scalar_one_or_none()
        if last_sync_at is None:
            return None

        if isinstance(last_sync_at, datetime):
            if last_sync_at.tzinfo is None:
                return last_sync_at.replace(tzinfo=timezone.utc)
            return last_sync_at.astimezone(timezone.utc)

        return None

    async def _should_refresh_match_events(self, db, match_id: int) -> bool:
        last_sync_at = await self._get_last_event_sync_at(db, match_id)
        if last_sync_at is None:
            return True

        now_utc = datetime.now(timezone.utc)
        elapsed_seconds = (now_utc - last_sync_at).total_seconds()
        return elapsed_seconds >= EVENT_REFRESH_INTERVAL_SECONDS

    async def _refresh_events_job(self):
        metrics = {
            "active_matches": 0,
            "processed_matches": 0,
            "synced_matches": 0,
            "skipped_matches": 0,
            "failed_matches": 0,
        }

        try:
            active_matches = await active_match_service.get_active_matches()
            metrics["active_matches"] = len(active_matches)
            if active_matches:
                logger.info("EVENT_REFRESH_START total_active=%s metrics=%s", len(active_matches), metrics)

            async with async_session() as db:
                for match_id in active_matches:
                    logger.debug("EVENT_REFRESH_MATCH match_id=%s", match_id)
                    status = await self._get_match_status_for_event_refresh(db, match_id)
                    status_upper = str(status or "").upper() if status is not None else None

                    if status_upper not in EVENT_REFRESH_ALLOWED_STATUSES:
                        metrics["skipped_matches"] += 1
                        logger.info(
                            "EVENT_REFRESH_SKIPPED match_id=%s reason=status_blocked status=%s",
                            match_id,
                            status_upper,
                        )
                        continue

                    metrics["processed_matches"] += 1

                    try:
                        should_refresh = await self._should_refresh_match_events(db, match_id)
                        if not should_refresh:
                            metrics["skipped_matches"] += 1
                            logger.info(
                                "EVENT_REFRESH_SKIPPED match_id=%s reason=refresh_window",
                                match_id,
                            )
                            continue

                        async def sync_events() -> dict:
                            result = await football_service.sync_match_events(db, match_id)
                            if result.get("success"):
                                await db.commit()
                                if result.get("analytics"):
                                    log_projection_transaction(result["analytics"], "committed")
                                try:
                                    await self.cache_service.delete(make_cache_key("match", match_id, "events"))
                                except Exception:
                                    logger.exception("EVENT_REFRESH_CACHE_INVALIDATION_FAILED match_id=%s", match_id)
                            return result

                        resource_locked, result = await run_with_resource_lock(
                            db,
                            "events",
                            match_id,
                            sync_events,
                        )
                        if not resource_locked:
                            metrics["skipped_matches"] += 1
                            logger.info("EVENT_REFRESH_SKIPPED match_id=%s reason=resource_lock_not_acquired", match_id)
                            continue

                        if result.get("success"):
                            metrics["synced_matches"] += 1
                            logger.info("EVENT_REFRESH_SYNCED match_id=%s", match_id)
                            continue

                        await db.rollback()
                        metrics["failed_matches"] += 1
                        logger.error(
                            "EVENT_REFRESH_FAILED match_id=%s reason=%s",
                            match_id,
                            result.get("message", "event_sync_failed"),
                        )
                    except Exception:
                        await db.rollback()
                        metrics["failed_matches"] += 1
                        logger.exception("EVENT_REFRESH_FAILED match_id=%s", match_id)

                SCHEDULER_JOB_RUNS.labels(job="refresh_events").inc()
                if active_matches:
                    logger.info("EVENT_REFRESH_COMPLETE metrics=%s", metrics)
                return metrics
        except Exception:
            SCHEDULER_JOB_ERRORS.labels(job="refresh_events").inc()
            logger.exception("Error in active events refresh job")
            return metrics

    async def _refresh_statistics_job(self):
        metrics = {
            "active_matches": 0,
            "processed_matches": 0,
            "synced_matches": 0,
            "skipped_matches": 0,
            "failed_matches": 0,
        }

        try:
            active_matches = await active_match_service.get_active_matches()
            metrics["active_matches"] = len(active_matches)
            if active_matches:
                logger.info("STATISTICS_REFRESH_START total_active=%s metrics=%s", len(active_matches), metrics)

            async with async_session() as db:
                for match_id in active_matches:
                    logger.debug("STATISTICS_REFRESH_MATCH match_id=%s", match_id)
                    status = await self._get_match_status_for_event_refresh(db, match_id)

                    if not status or status in STATISTICS_REFRESH_BLOCKED_STATUSES or status not in STATISTICS_REFRESH_ALLOWED_STATUSES:
                        metrics["skipped_matches"] += 1
                        logger.info(
                            "STATISTICS_REFRESH_SKIPPED match_id=%s reason=status_blocked status=%s",
                            match_id,
                            status,
                        )
                        continue

                    metrics["processed_matches"] += 1

                    try:
                        async def sync_statistics() -> dict:
                            result = await football_service.sync_match_statistics(db, match_id)
                            if result.get("success"):
                                await db.commit()
                                try:
                                    await self.cache_service.delete(make_cache_key("match", match_id, "statistics"))
                                except Exception:
                                    logger.exception("STATISTICS_REFRESH_CACHE_INVALIDATION_FAILED match_id=%s", match_id)
                            return result

                        resource_locked, result = await run_with_resource_lock(
                            db,
                            "statistics",
                            match_id,
                            sync_statistics,
                        )
                        if not resource_locked:
                            metrics["skipped_matches"] += 1
                            logger.info("STATISTICS_REFRESH_SKIPPED match_id=%s reason=resource_lock_not_acquired", match_id)
                            continue

                        if result.get("success"):
                            metrics["synced_matches"] += 1
                            logger.info("STATISTICS_REFRESH_SYNCED match_id=%s", match_id)
                            continue

                        await db.rollback()
                        metrics["failed_matches"] += 1
                        logger.error(
                            "STATISTICS_REFRESH_FAILED match_id=%s reason=%s",
                            match_id,
                            result.get("message", "statistics_sync_failed"),
                        )
                    except Exception:
                        await db.rollback()
                        metrics["failed_matches"] += 1
                        logger.exception("STATISTICS_REFRESH_FAILED match_id=%s", match_id)

                SCHEDULER_JOB_RUNS.labels(job="refresh_statistics").inc()
                if active_matches:
                    logger.info("STATISTICS_REFRESH_COMPLETE metrics=%s", metrics)
                return metrics
        except Exception:
            SCHEDULER_JOB_ERRORS.labels(job="refresh_statistics").inc()
            logger.exception("Error in active statistics refresh job")
            return metrics

    async def _refresh_h2h_job(self):
        metrics = {"candidate_matches": 0, "synced_matches": 0, "skipped_matches": 0, "failed_matches": 0}
        now = datetime.now(timezone.utc)
        window_start = now - timedelta(days=7)
        window_end = now + timedelta(days=7)

        try:
            async with async_session() as db:
                result = await db.execute(
                    select(Match)
                    .outerjoin(
                        MatchH2H,
                        MatchH2H.h2h_key
                        == func.concat(
                            func.least(Match.home_team_id, Match.away_team_id),
                            "-",
                            func.greatest(Match.home_team_id, Match.away_team_id),
                        ),
                    )
                    .where(Match.provider == "api-football")
                    .where(Match.match_time >= window_start)
                    .where(Match.match_time <= window_end)
                    .where(Match.home_team_id.is_not(None))
                    .where(Match.away_team_id.is_not(None))
                    .where(Match.home_team_id != Match.away_team_id)
                    .where(
                        or_(
                            MatchH2H.id.is_(None),
                            MatchH2H.updated_at.is_(None),
                            MatchH2H.updated_at <= now - timedelta(hours=24),
                        )
                    )
                    .order_by(Match.match_time.asc(), Match.local_match_id.asc())
                    .limit(100)
                )
                candidates = list(result.scalars().all())
                metrics["candidate_matches"] = len(candidates)
                processed_pairs = set()

                for match in candidates:
                    local_match_id = int(match.local_match_id)
                    team_pair = tuple(sorted((int(match.home_team_id), int(match.away_team_id))))
                    if team_pair in processed_pairs:
                        metrics["skipped_matches"] += 1
                        continue
                    processed_pairs.add(team_pair)

                    try:
                        async def sync_h2h() -> dict:
                            result = await football_service.refresh_h2h(db=db, match_id=local_match_id)
                            if "error" in result:
                                await db.rollback()
                                return result
                            await db.commit()
                            try:
                                await self.cache_service.delete(
                                    make_cache_key("match", "h2h", str(local_match_id))
                                )
                            except Exception:
                                logger.exception(
                                    "H2H_REFRESH_CACHE_INVALIDATION_FAILED local_match_id=%s",
                                    local_match_id,
                                )
                            return result

                        locked, refresh_result = await run_with_resource_lock(
                            db,
                            "h2h",
                            str(local_match_id),
                            sync_h2h,
                        )
                        if not locked:
                            metrics["skipped_matches"] += 1
                            logger.info(
                                "H2H_REFRESH_SKIPPED local_match_id=%s reason=resource_lock_not_acquired",
                                local_match_id,
                            )
                        elif "error" in refresh_result:
                            metrics["failed_matches"] += 1
                            logger.error("H2H_REFRESH_FAILED local_match_id=%s", local_match_id)
                        else:
                            metrics["synced_matches"] += 1
                            logger.info("H2H_REFRESH_SYNCED local_match_id=%s", local_match_id)
                    except Exception:
                        await db.rollback()
                        metrics["failed_matches"] += 1
                        logger.exception("H2H_REFRESH_FAILED local_match_id=%s", local_match_id)

                SCHEDULER_JOB_RUNS.labels(job="refresh_h2h").inc()
                logger.info("H2H_REFRESH_COMPLETE metrics=%s", metrics)
                return metrics
        except Exception:
            SCHEDULER_JOB_ERRORS.labels(job="refresh_h2h").inc()
            logger.exception("Error in H2H refresh job")
            return metrics

# Global scheduler instance
live_scheduler = LiveUpdateScheduler()