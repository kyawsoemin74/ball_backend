import asyncio
import logging
import signal

from app.monitoring import refresh_dependency_health, start_worker_metrics_server
from scheduler_service import start_scheduler, stop_scheduler


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5.5s [%(name)s] %(message)s",
)
logger = logging.getLogger(__name__)


async def run_worker() -> None:
    stop_event = asyncio.Event()

    def request_shutdown() -> None:
        logger.info("Background worker shutdown requested")
        stop_event.set()

    for shutdown_signal in (signal.SIGINT, signal.SIGTERM):
        signal.signal(shutdown_signal, lambda _signum, _frame: request_shutdown())

    start_worker_metrics_server(8001)
    postgres_ok, redis_ok = await refresh_dependency_health()
    logger.info("Worker dependency health: postgres=%s redis=%s", postgres_ok, redis_ok)
    start_scheduler()
    logger.info("Background worker started")

    try:
        await stop_event.wait()
    finally:
        stop_scheduler()
        logger.info("Background worker stopped")


if __name__ == "__main__":
    asyncio.run(run_worker())
