import asyncio
import logging
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO, format='%(levelname)s:%(name)s:%(message)s')

from app.services.football import football_service
from app.db import async_session
from app.models.match import Match
from sqlalchemy import select

async def main():
    broken_fixture = {
        'fixture': {'id': 999999999, 'date': '2026-07-10T00:00:00Z', 'status': {'short': 'NS', 'elapsed': 0}, 'venue': {'name': 'Test Venue', 'city': 'Test City'}},
        'league': {'id': 1, 'name': 'Test League', 'season': 2026, 'country': 'Test Country', 'flag': None, 'logo': None},
        'teams': {'home': {'id': 1, 'name': 'Home', 'logo': None}, 'away': {'id': 2, 'name': 'Away', 'logo': None}},
        'goals': {'home': 0, 'away': 0},
    }
    valid_fixture = {
        'fixture': {'id': 999999998, 'date': '2026-07-10T00:00:00Z', 'status': {'short': 'NS', 'elapsed': 0}, 'venue': {'name': 'Test Venue', 'city': 'Test City'}},
        'league': {'id': 1, 'name': 'Test League', 'season': 2026, 'country': 'Test Country', 'flag': None, 'logo': None},
        'teams': {'home': {'id': 1, 'name': 'Home', 'logo': None}, 'away': {'id': 2, 'name': 'Away', 'logo': None}},
        'goals': {'home': 0, 'away': 0},
    }
    async with async_session() as db:
        result, prewarm = await football_service.fixture_sync_service._process_sync_with_candidates(db, [broken_fixture, valid_fixture])
        print('RESULT', result)
        print('PREWARM', prewarm)
        row = await db.execute(select(Match).where(Match.match_id == 999999998))
        match = row.scalar_one_or_none()
        print('VALID_MATCH_PERSISTED', bool(match), match.match_id if match else None)

asyncio.run(main())
