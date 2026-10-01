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
from app.services.active_match_service import active_match_service
from app.services.analytics_projection_service import log_projection_transaction
from app.services.cache_service import CacheService
from app.services.football import football_service, FINISHED_STATUSES, LIVE_STATUSES
from app.services.odds_sync_service import (
    CACHE_INVALIDATION_FAILED,
    COMMIT_CONFIRMED,
    COMMIT_UNKNOWN,
    OddsSyncService,
)
from app.services.resource_lock import run_with_resource_lock
from app.services.season_identity import normalize_season

logger = logging.getLogger(__name__)

EVENT_REFRESH_ALLOWED_STATUSES = {"1H", "HT", "2H", "LIVE"}
EVENT_REFRESH_BLOCKED_STATUSES = {"NS", "FT", "AET", "PEN", "PST", "CANC", "ABD", "AWD", "WO"}
EVENT_FINALIZATION_RECOVERY_STATUSES = {"FT", "AET", "PEN", "PST", "CANC", "ABD", "AWD", "WO"}
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
RECENT_RECONCILIATION_LOCK_KEY = 9342005
_SCHEDULER_ADVISORY_LOCK_CONNECTIONS = "_scheduler_advisory_lock_connections"


async def commit_odds_refresh(db, odds_sync_service: OddsSyncService, local_match_id: int, refresh_result: dict) -> dict:
    """Commit an Odds snapshot and verify ambiguous commit outcomes without retrying."""
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
            return {"state": "failed", "reason": "COMMIT_FAILED"}

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
            return {"state": "failed", "reason": COMMIT_UNKNOWN}

        verification_state = verification.get("state")
        if verification_state == COMMIT_CONFIRMED:
            logger.info(
                "ODDS_COMMIT_CONFIRMED",
                extra={"local_match_id": local_match_id, "provider_fixture_id": context.get("provider_fixture_id"), "commit_state": COMMIT_CONFIRMED},
            )
            return {"state": "refreshed", "commit_state": COMMIT_CONFIRMED, "result": refresh_result}

        logger.warning(
            "ODDS_COMMIT_NOT_CONFIRMED",
            extra={"local_match_id": local_match_id, "provider_fixture_id": context.get("provider_fixture_id"), "commit_state": verification_state or COMMIT_UNKNOWN},
        )
        return {"state": "failed", "reason": verification_state or COMMIT_UNKNOWN}

    return {"state": "refreshed", "commit_state": "COMMITTED", "result": refresh_result}


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

class LiveUpdateScheduler:
    EXPECTED_JOB_IDS = (
        "sync_live_matches",
        "reconcile_recent_non_terminal",
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
                for key in ("success", "updated", "synced_matches", "failed_matches", "processed_matches"):
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
            self._reconcile_recent_non_terminal_job,
            trigger=IntervalTrigger(minutes=5),
            id="reconcile_recent_non_terminal",
            name="Reconcile Recent Fixtures",
            max_instances=1,
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
            trigger=IntervalTrigger(minutes=360),
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
            
    async def _should_sync_live_matches(self, db) -> bool:
        now = datetime.now(timezone.utc)
        past_threshold = now - timedelta(hours=24)
        discovery_horizon_end = now + timedelta(hours=24)

        candidate_result = await db.execute(
            select(func.count())
            .select_from(Match)
            .where(Match.match_time >= past_threshold)
            .where(Match.match_time <= discovery_horizon_end)
        )
        candidate_match_count = candidate_result.scalar_one()

        should_sync = candidate_match_count > 0

        logger.debug(
            "Live sync gate evaluated",
            extra={
                "candidate_match_count": candidate_match_count,
                "discovery_horizon_start": past_threshold.isoformat(),
                "discovery_horizon_end": discovery_horizon_end.isoformat(),
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
                    if not await self._should_sync_live_matches(db):
                        logger.debug("No near-start or active non-FT matches found; skipping live sync")
                        return

                    async def sync_live() -> dict:
                        try:
                            result = await football_service.sync_live_matches(db)
                            if result.get("success"):
                                await db.commit()
                                for match_id in result.get("final_event_sync_matches", []):
                                    event_cache_key = make_cache_key("match", match_id, "events")
                                    try:
                                        if not await self.cache_service.delete(event_cache_key):
                                            logger.warning(
                                                "FINAL_EVENT_CACHE_INVALIDATION_FAILED match_id=%s",
                                                match_id,
                                            )
                                    except Exception:
                                        logger.exception(
                                            "FINAL_EVENT_CACHE_INVALIDATION_FAILED match_id=%s",
                                            match_id,
                                        )
                                for match_id in result.get("final_statistics_sync_matches", []):
                                    statistics_cache_key = make_cache_key("match", match_id, "statistics")
                                    try:
                                        if not await self.cache_service.delete(statistics_cache_key):
                                            logger.warning(
                                                "FINAL_STATISTICS_CACHE_INVALIDATION_FAILED match_id=%s",
                                                match_id,
                                            )
                                    except Exception:
                                        logger.exception(
                                            "FINAL_STATISTICS_CACHE_INVALIDATION_FAILED match_id=%s",
                                            match_id,
                                        )
                                try:
                                    await football_service.apply_active_match_updates(result.get("active_match_updates"))
                                except Exception:
                                    logger.exception("LIVE_SYNC_ACTIVE_REGISTRY_UPDATE_FAILED")
                                try:
                                    await self.cache_service.delete(make_cache_key("live_matches"))
                                except Exception:
                                    logger.exception("LIVE_SYNC_CACHE_INVALIDATION_FAILED")
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
                        db, "live_sync", "global", sync_live
                    )
                    if not resource_locked:
                        logger.info("LIVE_SYNC_SKIPPED reason=resource_lock_not_acquired")
                        return
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

    async def _reconcile_recent_non_terminal_job(self):
        try:
            async with async_session() as db:
                if not await self._acquire_advisory_lock(db, RECENT_RECONCILIATION_LOCK_KEY):
                    logger.info("RECENT_RECONCILIATION_SKIPPED reason=lock_not_acquired")
                    return
                try:
                    async def reconcile() -> dict:
                        try:
                            result = await football_service.reconcile_recent_non_terminal(db)
                            if result.get("success"):
                                await db.commit()
                                await football_service.apply_active_match_updates(result.get("active_match_updates"))
                                try:
                                    await self.cache_service.delete(make_cache_key("live_matches"))
                                except Exception:
                                    logger.exception("RECENT_RECONCILIATION_CACHE_INVALIDATION_FAILED")
                                if result.get("final_lineup_candidates"):
                                    await football_service.finalize_pending_lineups(
                                        result["final_lineup_candidates"]
                                    )
                            else:
                                await db.rollback()
                            return result
                        except Exception:
                            await db.rollback()
                            raise

                    resource_locked, result = await run_with_resource_lock(
                        db, "fixture_query", "recent_reconciliation", reconcile
                    )
                    if resource_locked:
                        SCHEDULER_JOB_RUNS.labels(job="reconcile_recent_non_terminal").inc()
                        logger.info("RECENT_RECONCILIATION_COMPLETE result=%s", result)
                finally:
                    await self._release_advisory_lock(db, RECENT_RECONCILIATION_LOCK_KEY)
        except Exception:
            SCHEDULER_JOB_ERRORS.labels(job="reconcile_recent_non_terminal").inc()
            logger.exception("Error in recent fixture reconciliation job")

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
        }

        try:
            now_utc = datetime.now(timezone.utc)
            window_end = now_utc + timedelta(hours=ODDS_REFRESH_WINDOW_HOURS)
            async with async_session() as db:
                refreshed_cache_keys = []
                committed_projections = []
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
                        if match_id in self.pending_odds_cache_invalidations:
                            try:
                                recovered = await self.cache_service.delete(cache_key)
                            except Exception:
                                recovered = False
                            if recovered:
                                self.pending_odds_cache_invalidations.discard(match_id)
                                logger.info(
                                    "ODDS_CACHE_RECOVERY",
                                    extra={"local_match_id": match_id, "state": "CACHE_INVALIDATED"},
                                )
                            else:
                                logger.warning(
                                    "ODDS_CACHE_RECOVERY",
                                    extra={"local_match_id": match_id, "state": CACHE_INVALIDATION_FAILED},
                                )

                        latest_result = await db.execute(
                            select(Odds.last_updated)
                            .where(Odds.fixture_id == match_id)
                            .order_by(Odds.last_updated.desc())
                        )
                        latest_row = latest_result.first()
                        latest_update = latest_row[0] if latest_row else None

                        if latest_update and (now_utc - latest_update) < ODDS_REFRESH_MAX_AGE:
                            return {"state": "skipped"}

                        refresh_result = await football_service.odds_sync_service.refresh_odds(
                            db,
                            provider_fixture_id,
                            cache_key,
                            1800,
                            local_match_id=match_id,
                        )
                        if "error" in refresh_result:
                            await db.rollback()
                            return {"state": "failed", "reason": refresh_result.get("reason")}

                        commit_result = await commit_odds_refresh(
                            db,
                            football_service.odds_sync_service,
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
                        return commit_result

                    try:
                        resource_locked, refresh_result = await run_with_resource_lock(
                            db,
                            "odds",
                            match_id,
                            refresh_fixture,
                        )
                        if not resource_locked:
                            metrics["skipped_matches"] += 1
                            logger.info("ODDS_REFRESH_SKIPPED match_id=%s reason=resource_lock_not_acquired", match_id)
                            continue

                        state = refresh_result.get("state")
                        if state == "skipped":
                            metrics["skipped_matches"] += 1
                        elif state == "failed":
                            metrics["failed_matches"] += 1
                            reason = refresh_result.get("reason")
                            logger.error(
                                "ODDS_REFRESH_FAILED match_id=%s reason=%s retry_classification=%s",
                                match_id,
                                reason,
                                football_service.odds_sync_service.classify_retry_state(reason),
                            )
                        else:
                            committed_projection = refresh_result["result"].get("analytics")
                            if committed_projection:
                                log_projection_transaction(committed_projection, "committed")
                            metrics["refreshed_matches"] += 1
                            logger.debug("ODDS_REFRESH_SYNCED match_id=%s", match_id)
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

                    if not status_upper or (
                        status_upper in EVENT_REFRESH_BLOCKED_STATUSES
                        and status_upper not in EVENT_FINALIZATION_RECOVERY_STATUSES
                        and status_upper not in EVENT_REFRESH_ALLOWED_STATUSES
                    ):
                        metrics["skipped_matches"] += 1
                        logger.info(
                            "EVENT_REFRESH_SKIPPED match_id=%s reason=status_blocked status=%s",
                            match_id,
                            status_upper,
                        )
                        continue

                    metrics["processed_matches"] += 1

                    try:
                        should_refresh = status_upper in EVENT_FINALIZATION_RECOVERY_STATUSES or await self._should_refresh_match_events(db, match_id)
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