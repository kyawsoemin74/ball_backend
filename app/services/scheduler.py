import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from app.db.database import SessionLocal
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
            
        # Add job to run every 2 minutes
        self.scheduler.add_job(
            self._sync_live_matches_job,
            trigger=IntervalTrigger(minutes=2),
            id="sync_live_matches",
            name="Sync Live Matches",
            max_instances=1  # Prevent overlapping jobs
        )
        
        self.scheduler.start()
        self.is_running = True
        logger.info("Live update scheduler started - will sync every 2 minutes")
        
    def stop(self):
        """Stop the live update scheduler"""
        if self.is_running:
            self.scheduler.shutdown(wait=True)
            self.is_running = False
            logger.info("Live update scheduler stopped")
            
    async def _sync_live_matches_job(self):
        """Job function to sync live matches"""
        try:
            db = SessionLocal()
            try:
                result = await football_service.sync_live_matches(db)
                if result.get("success"):
                    if result.get("updated", 0) > 0:
                        logger.info(f"Live sync completed: {result}")
                    # If no updates, don't log to avoid spam
                else:
                    logger.error(f"Live sync failed: {result}")
            finally:
                db.close()
        except Exception as e:
            logger.error(f"Error in live sync job: {e}")
            # Continue running even if one job fails

# Global scheduler instance
live_scheduler = LiveUpdateScheduler()