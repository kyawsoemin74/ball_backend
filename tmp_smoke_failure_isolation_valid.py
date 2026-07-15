import asyncio
import copy
import logging
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO, format='%(levelname)s:%(name)s:%(message)s')

from app.services.football import football_service
from app.db import async_session

async def main():
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    provider_result = await football_service.get_fixtures_by_date(today)
    fixtures = provider_result.get('response', []) if provider_result else []
    if not fixtures:
        print('NO_FIXTURES_FROM_PROVIDER')
        return

    broken_fixture = {'league': {'id': 1, 'name': 'Broken'}, 'fixture': {'id': 999999999, 'date': None}}
    test_fixtures = [broken_fixture] + [copy.deepcopy(fixtures[0])]
    async with async_session() as db:
        result, prewarm = await football_service.fixture_sync_service._process_sync_with_candidates(db, test_fixtures)
        print('RESULT', result)
        print('PREWARM', prewarm)

asyncio.run(main())
