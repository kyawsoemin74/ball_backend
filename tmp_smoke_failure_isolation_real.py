import asyncio
import logging
from app.services.football import football_service
from app.db import async_session
from sqlalchemy import select
from app.models.match import Match

logging.basicConfig(level=logging.INFO, format='%(levelname)s:%(name)s:%(message)s')

async def main():
    malformed_fixture = {
        'fixture': {'id': 999999991, 'status': {'short': 'NS', 'elapsed': 0}, 'venue': {'name': 'Bad Venue', 'city': 'Bad City'}},
        'league': {'id': 1, 'name': 'Broken League', 'season': 2026, 'country': 'Test Country', 'flag': None, 'logo': None},
        'teams': {'home': {'id': 1, 'name': 'Home', 'logo': None}, 'away': {'id': 2, 'name': 'Away', 'logo': None}},
        'goals': {'home': 0, 'away': 0},
    }
    valid_fixture = {
        'fixture': {'id': 999999992, 'date': '2026-07-10T00:00:00Z', 'status': {'short': 'NS', 'elapsed': 0}, 'venue': {'name': 'Good Venue', 'city': 'Good City'}},
        'league': {'id': 1, 'name': 'Good League', 'season': 2026, 'country': 'Test Country', 'flag': None, 'logo': None},
        'teams': {'home': {'id': 1, 'name': 'Home', 'logo': None}, 'away': {'id': 2, 'name': 'Away', 'logo': None}},
        'goals': {'home': 0, 'away': 0},
    }
    async with async_session() as db:
        result, prewarm = await football_service.fixture_sync_service._process_sync_with_candidates(db, [malformed_fixture, valid_fixture])
        print('RESULT', result)
        print('PREWARM', prewarm)
        row = await db.execute(select(Match).where(Match.match_id == 999999992))
        match = row.scalar_one_or_none()
        print('VALID_MATCH_PERSISTED', bool(match), match.match_id if match else None)
        row2 = await db.execute(select(Match).where(Match.match_id == 999999991))
        match2 = row2.scalar_one_or_none()
        print('MALFORMED_MATCH_PERSISTED', bool(match2), match2.match_id if match2 else None)

asyncio.run(main())
