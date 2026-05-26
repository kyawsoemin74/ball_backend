import logging

from app.monitoring import SCHEDULER_UP
from app.services.scheduler import LiveUpdateScheduler, live_scheduler

logger = logging.getLogger(__name__)


def get_scheduler() -> LiveUpdateScheduler:
    return live_scheduler


def start_scheduler() -> None:
    logger.info("Starting dedicated scheduler service")
    live_scheduler.start()
    SCHEDULER_UP.set(1)


def stop_scheduler() -> None:
    logger.info("Stopping dedicated scheduler service")
    live_scheduler.stop()
    SCHEDULER_UP.set(0)
