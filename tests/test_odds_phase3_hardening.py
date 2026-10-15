import asyncio
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select

from app.db import async_session
from app.models.league import League
from app.models.match import Match
from app.models.odds import Odds
from app.services.odds_reconciliation import RESOLVED
from app.services.odds_sync_service import CANDIDATE_READY, PERSISTENCE_FAILED, OddsSyncService
from app.services.resource_lock import acquire_resource_lock


@pytest.mark.asyncio
async def test_resolve_attempt_without_candidate_uses_canonical_resolved_state():
    repository = SimpleNamespace(
        resolve_reconciliation=AsyncMock(return_value=True)
    )
    service = OddsSyncService.__new__(OddsSyncService)
    service.retry_state_repository = repository

    await service.resolve_attempt_without_candidate(
        SimpleNamespace(),
        1507,
        "attempt-1507",
        "PROVIDER_FAILED",
    )

    repository.resolve_reconciliation.assert_awaited_once()
    assert repository.resolve_reconciliation.await_args.kwargs["reconciliation_state"] == RESOLVED


async def _create_match(db, provider_fixture_id: int) -> Match:
    league = League(
        provider="api-football",
        provider_id=f"phase3-{uuid.uuid4().hex[:12]}",
        name=f"Phase3 League {provider_fixture_id}",
        type="domestic",
        national=False,
        display_order=99,
    )
    db.add(league)
    await db.flush()

    match = Match(
        provider="api-football",
        provider_fixture_id=provider_fixture_id,
        league_id=league.league_id,
        season=2026,
        league_name=league.name,
        home_team="Home Team",
        away_team="Away Team",
        home_score=0,
        away_score=0,
        match_time=datetime.now(timezone.utc) + timedelta(days=1),
        status="NS",
    )
    db.add(match)
    await db.flush()
    return match


@pytest.mark.asyncio
async def test_db_backed_rollback_preserves_odds_and_outer_transaction_boundary():
    provider_fixture_id = 20001 + (uuid.uuid4().int % 10000)
    async with async_session() as setup_db:
        match = await _create_match(setup_db, provider_fixture_id)
        setup_db.add(
            Odds(
                fixture_id=match.local_match_id,
                bookmaker_name="1xBet",
                market_name="MATCH_WINNER",
                selection="Home",
                odd_value="1.80",
                myanmar_odd=None,
                last_updated=datetime.now(timezone.utc),
            )
        )
        await setup_db.commit()

    async with async_session() as db:
        before = await db.scalar(
            select(func.count()).select_from(Odds).where(
                Odds.fixture_id == match.local_match_id
            )
        )
        provider_payload = {
            "response": [
                {
                    "fixture": {"id": provider_fixture_id},
                    "bookmakers": [
                        {
                            "id": 11,
                            "name": "1xBet",
                            "bets": [
                                {
                                    "id": 1,
                                    "name": "Match Winner",
                                    "values": [
                                        {"value": "Home", "odd": "1.85"},
                                        {"value": "Draw", "odd": "3.10"},
                                        {"value": "Away", "odd": "4.20"},
                                    ],
                                },
                                {
                                    "id": 4,
                                    "name": "Asian Handicap",
                                    "values": [
                                        {"value": "Home -0.5", "odd": "1.91"},
                                        {"value": "Away -0.5", "odd": "1.95"},
                                    ],
                                },
                                {
                                    "id": 5,
                                    "name": "Goals Over/Under",
                                    "values": [
                                        {"value": "Over 2.5", "odd": "1.95"},
                                        {"value": "Under 2.5", "odd": "1.90"},
                                    ],
                                },
                            ],
                        }
                    ],
                }
            ]
        }

        async def fake_provider(_fixture_id):
            return provider_payload

        service = OddsSyncService(SimpleNamespace(client=None))
        service.odds_provider = SimpleNamespace(get_match_odds=fake_provider)
        result = await service.refresh_odds(
            db,
            provider_fixture_id,
            "odds:test-rollback",
            1800,
            local_match_id=match.local_match_id,
        )
        assert result["status"] == CANDIDATE_READY
        await db.rollback()

        after = await db.scalar(
            select(func.count()).select_from(Odds).where(
                Odds.fixture_id == match.local_match_id
            )
        )
        assert before == after


@pytest.mark.asyncio
async def test_sqlite_projection_failure_rolls_back_odds_and_analytics_together():
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """
        CREATE TABLE odds (
            fixture_id INTEGER,
            market_name TEXT,
            selection TEXT,
            odd_value TEXT,
            myanmar_odd TEXT
        );
        CREATE TABLE analytics_odds (
            match_id INTEGER,
            market_name TEXT,
            selection TEXT,
            odd_value TEXT,
            myanmar_odd TEXT
        );
        """
    )
    previous_rows = [
        ("MATCH_WINNER", "Home", "1.70", None),
        ("MATCH_WINNER", "Draw", "3.30", None),
        ("MATCH_WINNER", "Away", "4.60", None),
        ("ASIAN_HANDICAP", "Home -0.5", "1.90", "HOME"),
        ("ASIAN_HANDICAP", "Away -0.5", "1.90", "AWAY"),
        ("GOALS_OVER_UNDER", "Over 2.5", "1.90", "OVER"),
        ("GOALS_OVER_UNDER", "Under 2.5", "1.90", "UNDER"),
    ]
    connection.executemany(
        "INSERT INTO odds VALUES (1508, ?, ?, ?, ?)",
        previous_rows,
    )
    connection.executemany(
        "INSERT INTO analytics_odds VALUES (1508, ?, ?, ?, ?)",
        previous_rows,
    )
    connection.commit()

    class SQLiteSession:
        async def scalar(self, _statement):
            return SimpleNamespace(
                local_match_id=1508,
                provider="api-football",
                provider_fixture_id=1557421,
            )

        async def flush(self):
            return None

        async def rollback(self):
            connection.rollback()

    class SQLiteOddsRepository:
        async def replace_fixture_odds(self, _db, fixture_id, rows):
            connection.execute("DELETE FROM odds WHERE fixture_id = ?", (fixture_id,))
            connection.executemany(
                "INSERT INTO odds VALUES (?, ?, ?, ?, ?)",
                [
                    (
                        row["fixture_id"],
                        row["market_name"],
                        row["selection"],
                        row["odd_value"],
                        row["myanmar_odd"],
                    )
                    for row in rows
                ],
            )

    class SQLiteAnalyticsProjection:
        odds_repository = SimpleNamespace()

        async def project_odds(self, _db, match_id, rows):
            connection.execute("DELETE FROM analytics_odds WHERE match_id = ?", (match_id,))
            connection.executemany(
                "INSERT INTO analytics_odds VALUES (?, ?, ?, ?, ?)",
                [
                    (
                        match_id,
                        row["market_name"],
                        row["selection"],
                        str(row["odd_value"]),
                        row["myanmar_odd"],
                    )
                    for row in rows
                ],
            )
            raise RuntimeError("injected analytics projection failure")

    provider = SimpleNamespace(
        get_match_odds=AsyncMock(
            return_value={
                "response": [
                    {
                        "fixture": {"id": 1557421},
                        "bookmakers": [
                            {
                                "id": 11,
                                "name": "1xBet",
                                "bets": [
                                    {
                                        "id": 1,
                                        "name": "Match Winner",
                                        "values": [
                                            {"value": "Home", "odd": "1.80"},
                                            {"value": "Draw", "odd": "3.10"},
                                            {"value": "Away", "odd": "4.20"},
                                        ],
                                    },
                                    {
                                        "id": 4,
                                        "name": "Asian Handicap",
                                        "values": [
                                            {"value": "Home -0.5", "odd": "1.91"},
                                            {"value": "Away -0.5", "odd": "1.95"},
                                        ],
                                    },
                                    {
                                        "id": 5,
                                        "name": "Goals Over/Under",
                                        "values": [
                                            {"value": "Over 2.5", "odd": "1.95"},
                                            {"value": "Under 2.5", "odd": "1.90"},
                                        ],
                                    },
                                ],
                            }
                        ],
                    }
                ]
            }
        )
    )
    sync_service = OddsSyncService(
        SimpleNamespace(client=None),
        odds_provider=provider,
        odds_repository=SQLiteOddsRepository(),
        analytics_projection_service=SQLiteAnalyticsProjection(),
    )
    db = SQLiteSession()
    try:
        result = await sync_service.refresh_odds(
            db,
            1557421,
            "odds:test-atomic-rollback",
            1800,
            local_match_id=1508,
        )
        assert result["status"] == PERSISTENCE_FAILED

        staged_odds = connection.execute(
            "SELECT market_name, selection, odd_value, myanmar_odd "
            "FROM odds WHERE fixture_id = 1508 ORDER BY market_name, selection"
        ).fetchall()
        staged_analytics = connection.execute(
            "SELECT market_name, selection, odd_value, myanmar_odd "
            "FROM analytics_odds WHERE match_id = 1508 ORDER BY market_name, selection"
        ).fetchall()
        assert staged_odds != sorted(previous_rows)
        assert staged_analytics != sorted(previous_rows)

        await db.rollback()

        actual_odds = connection.execute(
            "SELECT market_name, selection, odd_value, myanmar_odd "
            "FROM odds WHERE fixture_id = 1508 ORDER BY market_name, selection"
        ).fetchall()
        actual_analytics = connection.execute(
            "SELECT market_name, selection, odd_value, myanmar_odd "
            "FROM analytics_odds WHERE match_id = 1508 ORDER BY market_name, selection"
        ).fetchall()
        expected_rows = sorted(previous_rows)
        assert actual_odds == expected_rows
        assert actual_analytics == expected_rows
    finally:
        connection.close()


@pytest.mark.asyncio
async def test_resource_lock_is_transaction_scoped_and_released():
    lock_key = 30001 + (uuid.uuid4().int % 10000)
    async with async_session() as first_db:
        handle = await acquire_resource_lock(first_db, "odds", lock_key)
        assert handle is not None
        async with async_session() as second_db:
            assert await acquire_resource_lock(second_db, "odds", lock_key) is None

    async with async_session() as next_db:
        assert await acquire_resource_lock(next_db, "odds", lock_key) is not None


def test_session_factory_does_not_reuse_closed_loop_across_asyncio_runs():
    async def probe() -> None:
        async with async_session() as db:
            handle = await acquire_resource_lock(
                db, "odds", 35001 + (uuid.uuid4().int % 10000)
            )
            assert handle is not None
            await db.commit()

    asyncio.run(probe())
    asyncio.run(probe())
