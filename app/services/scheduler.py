import logging
from datetime import datetime, timezone, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from app.db import SessionLocal
from app.models.match import Match
from app.services.football import football_service

logger = logging.getLogger(__name__)

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
            trigger=IntervalTrigger(seconds=2400),
            id="sync_live_matches",
            name="Sync Live Matches",
            max_instances=1  # Prevent overlapping jobs
        )
        
        self.scheduler.start()
        self.is_running = True
        logger.info("Live update scheduler started - syncing every 2400 seconds")
        
    def stop(self):
        """Stop the live update scheduler"""
        if self.is_running:
            self.scheduler.shutdown(wait=True)
            self.is_running = False
            logger.info("Live update scheduler stopped")
            
    def _should_sync_live_matches(self, db) -> bool:
        now = datetime.now(timezone.utc)
        threshold = now + timedelta(minutes=5)
        count = db.query(Match).filter(
            Match.match_time <= threshold,
            Match.status != "FT"
        ).count()
        logger.debug(f"Live sync check found {count} upcoming/non-finished matches")
        return count > 0

    async def _sync_live_matches_job(self):
        """Job function to sync live matches"""
        try:
            db = SessionLocal()
            try:
                if not self._should_sync_live_matches(db):
                    logger.debug("No near-start or active non-FT matches found; skipping live sync")
                    return

                result = await football_service.sync_live_matches(db)
                if result.get("success"):
                    if result.get("updated", 0) > 0:
                        logger.info(f"Live sync completed: {result}")
                else:
                    logger.error(f"Live sync failed: {result}")
            finally:
                db.close()
        except Exception as e:
            logger.error(f"Error in live sync job: {e}")
            # Continue running even if one job fails

# Global scheduler instance
live_scheduler = LiveUpdateScheduler()