import asyncio
import logging

from scheduler_service import start_scheduler, stop_scheduler
from app.monitoring import start_worker_metrics_server
from app.services.notification import notification_worker


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-5.5s [%(name)s] %(message)s",
    )


async def main() -> None:
    _configure_logging()
    start_worker_metrics_server(8001)
    start_scheduler()
    notification_task = asyncio.create_task(notification_worker.start())
    try:
        while True:
            await asyncio.sleep(60)
    except asyncio.CancelledError:
        pass
    finally:
        await notification_worker.stop()
        notification_task.cancel()
        try:
            await notification_task
        except asyncio.CancelledError:
            pass
        stop_scheduler()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
