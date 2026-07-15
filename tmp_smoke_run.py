import asyncio
import logging
import os
import sys
from datetime import datetime, timezone, timedelta

logging.basicConfig(level=logging.INFO, format='%(levelname)s:%(name)s:%(message)s')

from app.services.scheduler import live_scheduler

async def main():
    print('RUN_START', datetime.now(timezone.utc).isoformat())
    await live_scheduler._sync_daily_fixtures_job()
    print('RUN_END', datetime.now(timezone.utc).isoformat())

asyncio.run(main())
