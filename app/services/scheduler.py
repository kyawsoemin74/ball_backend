import logging
from datetime import datetime, timezone, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select, func
from app.db import async_session
from app.models.match import Match
from app.monitoring import SCHEDULER_JOB_ERRORS, SCHEDULER_JOB_RUNS
from app.services.football import football_service, FINISHED_STATUSES

logger = logging.getLogger(__name__)

# Myanmar Timezone Offset (UTC+6:30)
MM_TZ = timezone(timedelta(hours=6, minutes=30))

class LiveUpdateScheduler:
    def __init__(self):
        self.scheduler = AsyncIOScheduler()
        self.is_running = False
        
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
        
        self.scheduler.start()
        self.is_running = True
        logger.info("Live update scheduler started - syncing every 60 seconds")
        
    def stop(self):
        """Stop the live update scheduler"""
        if self.is_running:
            self.scheduler.shutdown(wait=True)
            self.is_running = False
            logger.info("Live update scheduler stopped")
            
    async def _should_sync_live_matches(self, db) -> bool:
        now = datetime.now(timezone.utc)
        future_threshold = now + timedelta(minutes=5)
        past_threshold = now - timedelta(hours=24)

        result = await db.execute(
            select(func.count())
            .select_from(Match)
            .where(Match.match_time <= future_threshold)
            .where(Match.match_time >= past_threshold)
            .where(Match.status.notin_(FINISHED_STATUSES))
        )
        count = result.scalar_one()
        logger.debug(f"Live sync check found {count} upcoming/non-finished matches")
        return count > 0

    async def _sync_live_matches_job(self):
        """Job function to sync live matches"""
        try:
            async with async_session() as db:
                if not await self._should_sync_live_matches(db):
                    logger.debug("No near-start or active non-FT matches found; skipping live sync")
                    return

                result = await football_service.sync_live_matches(db)
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
                SCHEDULER_JOB_RUNS.labels(job="sync_daily_fixtures").inc()
                logger.info(f"Automatic daily sync completed: {result}")
        except Exception as e:
            SCHEDULER_JOB_ERRORS.labels(job="sync_daily_fixtures").inc()
            logger.error(f"Error in daily sync job: {e}")

# Global scheduler instance
live_scheduler = LiveUpdateScheduler()