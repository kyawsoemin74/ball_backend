import logging
from datetime import datetime, timezone, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select, func
from app.cache import make_cache_key
from app.core.config import settings
from app.db import async_session
from app.models.allowed_league import AllowedLeague
from app.models.match import Match
from app.monitoring import SCHEDULER_JOB_ERRORS, SCHEDULER_JOB_RUNS
from app.repositories.lineup_refresh_state_repository import LineupRefreshStateRepository
from app.services.active_match_service import active_match_service
from app.services.cache_service import CacheService
from app.services.football import football_service, FINISHED_STATUSES, LIVE_STATUSES

logger = logging.getLogger(__name__)

EVENT_REFRESH_ALLOWED_STATUSES = {"1H", "HT", "2H", "LIVE"}
EVENT_REFRESH_BLOCKED_STATUSES = {"NS", "FT", "AET", "PEN", "PST", "CANC", "ABD", "AWD", "WO"}
STATISTICS_REFRESH_ALLOWED_STATUSES = {"1H", "HT", "2H", "LIVE"}
STATISTICS_REFRESH_BLOCKED_STATUSES = {"NS", "FT", "AET", "PEN", "PST", "CANC", "ABD", "AWD", "WO"}

# Myanmar Timezone Offset (UTC+6:30)
MM_TZ = timezone(timedelta(hours=6, minutes=30))

class LiveUpdateScheduler:
    def __init__(self):
        self.scheduler = AsyncIOScheduler()
        self.is_running = False
        self.lineup_refresh_state_repository = LineupRefreshStateRepository()
        self.cache_service = CacheService()
        
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
            self._refresh_lineups_job,
            trigger=IntervalTrigger(minutes=15),
            id="refresh_lineups",
            name="Refresh Lineups",
            max_instances=1,
        )

        self.scheduler.add_job(
            self._refresh_events_job,
            trigger=IntervalTrigger(seconds=60),
            id="refresh_events",
            name="Refresh Active Match Events",
            max_instances=1,
        )

        self.scheduler.add_job(
            self._refresh_statistics_job,
            trigger=IntervalTrigger(seconds=120),
            id="refresh_statistics",
            name="Refresh Active Match Statistics",
            max_instances=1,
        )
        
        self.scheduler.start()
        self.is_running = True
        logger.info("Live update scheduler started")
        
    def stop(self):
        """Stop the live update scheduler"""
        if self.is_running:
            self.scheduler.shutdown(wait=True)
            self.is_running = False
            logger.info("Live update scheduler stopped")
            
    async def _should_sync_live_matches(self, db) -> bool:
        now = datetime.now(timezone.utc)
        past_threshold = now - timedelta(hours=24)
        kickoff_start = now - timedelta(minutes=10)
        kickoff_end = now + timedelta(minutes=10)

        live_result = await db.execute(
            select(func.count())
            .select_from(Match)
            .where(Match.match_time >= past_threshold)
            .where(Match.status.in_(LIVE_STATUSES))
        )
        live_match_count = live_result.scalar_one()

        kickoff_result = await db.execute(
            select(func.count())
            .select_from(Match)
            .where(Match.match_time >= kickoff_start)
            .where(Match.match_time <= kickoff_end)
        )
        kickoff_window_count = kickoff_result.scalar_one()

        should_sync = live_match_count > 0 or kickoff_window_count > 0

        logger.info(
            "Live sync gate evaluated",
            extra={
                "live_match_count": live_match_count,
                "kickoff_window_count": kickoff_window_count,
                "should_sync": should_sync,
            },
        )
        return should_sync

    async def _sync_live_matches_job(self):
        """Job function to sync live matches"""
        try:
            async with async_session() as db:
                if not await self._should_sync_live_matches(db):
                    logger.debug("No near-start or active non-FT matches found; skipping live sync")
                    return

                result = await football_service.sync_live_matches(db)
                await db.commit()
                SCHEDULER_JOB_RUNS.labels(job="sync_live_matches").inc()
                if result.get("success"):
                    if result.get("updated", 0) > 0:
                        logger.info(f"Live sync completed: {result}")
                else:
                    logger.error(f"Live sync failed: {result}")
        except Exception as e:
            SCHEDULER_JOB_ERRORS.labels(job="sync_live_matches").inc()
            logger.error(f"Error in live sync job: {e}")
            # Continue running even if one job fails

    async def _sync_daily_fixtures_job(self):
        """Job function to sync all fixtures for the current day"""
        try:
            # Get today's date in Myanmar timezone (YYYY-MM-DD)
            today = datetime.now(MM_TZ).strftime("%Y-%m-%d")
            async with async_session() as db:
                logger.info(f"Starting automatic daily sync for {today}")
                result = await football_service.sync_daily_fixtures(db, today)
                await db.commit()
                SCHEDULER_JOB_RUNS.labels(job="sync_daily_fixtures").inc()
                logger.info(f"Automatic daily sync completed: {result}")
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
                logger.info(f"Starting daily repair sync for {yesterday_str} and {today_str}")

                result_yesterday = await football_service.sync_daily_fixtures(db, yesterday_str)
                await db.commit()
                logger.info(f"Daily repair sync for {yesterday_str} completed: {result_yesterday}")

                result_today = await football_service.sync_daily_fixtures(db, today_str)
                await db.commit()
                logger.info(f"Daily repair sync for {today_str} completed: {result_today}")

                SCHEDULER_JOB_RUNS.labels(job="repair_daily_matches").inc()
        except Exception as e:
            SCHEDULER_JOB_ERRORS.labels(job="repair_daily_matches").inc()
            logger.error(f"Error in daily repair sync job: {e}")

    async def _get_allowed_standings_pairs(self, db) -> list[tuple[int, int]]:
        result = await db.execute(
            select(Match.league_id, Match.season)
            .join(AllowedLeague, AllowedLeague.league_id == Match.league_id)
            .where(Match.season.is_not(None))
            .distinct()
            .order_by(Match.league_id.asc(), Match.season.asc())
        )
        return [(int(league_id), int(season)) for league_id, season in result.all()]

    async def _refresh_standings_job(self):
        metrics = {
            "processed_pairs": 0,
            "success_pairs": 0,
            "failed_pairs": 0,
        }

        try:
            async with async_session() as db:
                pairs = await self._get_allowed_standings_pairs(db)
                logger.info("STANDINGS_REFRESH_START total_pairs=%s metrics=%s", len(pairs), metrics)

                for league_id, season in pairs:
                    metrics["processed_pairs"] += 1
                    logger.info("STANDINGS_REFRESH_LEAGUE league_id=%s season=%s", league_id, season)

                    try:
                        result = await football_service.sync_standings(db, league_id, season)
                        if result.get("success"):
                            await db.commit()
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
        except Exception:
            SCHEDULER_JOB_ERRORS.labels(job="refresh_standings").inc()
            logger.exception("Error in standings refresh job")
            return metrics

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
                    logger.info("LINEUP_REFRESH_MATCH match_id=%s", match_id)

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

                        result = await football_service.sync_match_lineup(db, match_id)
                        if result.get("success"):
                            await self.lineup_refresh_state_repository.touch(db, match_id, refreshed_at=now_utc)
                            await db.commit()
                            if result.get("skipped"):
                                metrics["skipped_matches"] += 1
                                logger.info(
                                    "LINEUP_REFRESH_SKIPPED match_id=%s reason=%s status=%s",
                                    match_id,
                                    result.get("reason"),
                                    result.get("status"),
                                )
                            else:
                                metrics["synced_matches"] += 1
                                logger.info("LINEUP_REFRESH_SYNCED match_id=%s", match_id)
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
            logger.info("EVENT_REFRESH_START total_active=%s metrics=%s", len(active_matches), metrics)

            async with async_session() as db:
                for match_id in active_matches:
                    logger.info("EVENT_REFRESH_MATCH match_id=%s", match_id)
                    status = await self._get_match_status_for_event_refresh(db, match_id)

                    if not status or status in EVENT_REFRESH_BLOCKED_STATUSES or status not in EVENT_REFRESH_ALLOWED_STATUSES:
                        metrics["skipped_matches"] += 1
                        logger.info(
                            "EVENT_REFRESH_SKIPPED match_id=%s reason=status_blocked status=%s",
                            match_id,
                            status,
                        )
                        continue

                    metrics["processed_matches"] += 1

                    try:
                        result = await football_service.sync_match_events(db, match_id)
                        if result.get("success"):
                            await db.commit()
                            await self.cache_service.delete(make_cache_key("match", match_id, "events"))
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
            logger.info("STATISTICS_REFRESH_START total_active=%s metrics=%s", len(active_matches), metrics)

            async with async_session() as db:
                for match_id in active_matches:
                    logger.info("STATISTICS_REFRESH_MATCH match_id=%s", match_id)
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
                        result = await football_service.sync_match_statistics(db, match_id)
                        if result.get("success"):
                            await db.commit()
                            await self.cache_service.delete(make_cache_key("match", match_id, "statistics"))
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
                logger.info("STATISTICS_REFRESH_COMPLETE metrics=%s", metrics)
                return metrics
        except Exception:
            SCHEDULER_JOB_ERRORS.labels(job="refresh_statistics").inc()
            logger.exception("Error in active statistics refresh job")
            return metrics

# Global scheduler instance
live_scheduler = LiveUpdateScheduler()