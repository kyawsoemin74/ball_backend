import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.cache import make_cache_key
from app.models.league_season import LeagueSeason
from app.models.standing import Standings
from app.schemas.standing import StandingResponse
from app.services.season_identity import normalize_season
from app.services.scheduler import LiveUpdateScheduler
from app.services.standing_sync_service import StandingSyncService


class FakeDatabase:
    async def flush(self):
        return None


class FakeCache:
    def __init__(self):
        self.deleted_keys = []

    def delete_sync(self, key):
        self.deleted_keys.append(key)


def make_service(provider_result):
    provider = SimpleNamespace(get_league_standings=AsyncMock(return_value=provider_result))
    team_service = SimpleNamespace(
        resolve_provider_teams=AsyncMock(
            return_value={"unresolved": [], "resolved": {901: 45}}
        )
    )
    cache_service = FakeCache()
    service = StandingSyncService(
        provider,
        team_service,
        cache_service,
        analytics_projection_service=SimpleNamespace(),
    )
    service.allowed_league_repository = SimpleNamespace(
        get_allowed_ids=AsyncMock(return_value={17})
    )
    service.league_repository = SimpleNamespace(
        get_by_id=AsyncMock(
            return_value=SimpleNamespace(provider="api-football", provider_id="812")
        )
    )
    service.league_season_repository = SimpleNamespace(
        get_by_league_and_season=AsyncMock(
            return_value=SimpleNamespace(id=501, season="2025", provider="api-football")
        )
    )
    service.standing_repository = SimpleNamespace(upsert_for_league_season=AsyncMock())
    return service, provider, cache_service


def test_normalize_season_requires_a_positive_integer_year():
    assert normalize_season(" 02025 ") == "2025"
    assert normalize_season(2025) == "2025"
    for invalid in (True, 0, -1, "", "2025/26", 2025.0):
        with pytest.raises(ValueError):
            normalize_season(invalid)


def test_sync_uses_provider_identity_and_league_season_scope():
    payload = {
        "response": [
            {
                "league": {
                    "standings": [[
                        {
                            "team": {"id": 901, "name": "Example FC", "logo": None},
                            "rank": 1,
                            "points": 3,
                            "all": {
                                "played": 1,
                                "win": 1,
                                "draw": 0,
                                "lose": 0,
                                "goals": {"for": 2, "against": 1},
                            },
                            "goalsDiff": 1,
                        }
                    ]]
                }
            }
        ]
    }
    service, provider, cache_service = make_service(payload)

    async def run():
        result = await service.sync_standings(FakeDatabase(), 17, "02025")
        assert result["success"] is True
        assert result["updated"] == 1
        provider.get_league_standings.assert_awaited_once_with(812, 2025)
        write_call = service.standing_repository.upsert_for_league_season.await_args
        assert write_call.args[1] == 501
        assert write_call.args[2][0]["league_season_id"] == 501
        assert write_call.args[2][0]["team_id"] == 45
        assert cache_service.deleted_keys == [make_cache_key("standings", 501)]

    asyncio.run(run())


def test_sync_does_not_call_provider_without_league_season():
    service, provider, _ = make_service(None)
    service.league_season_repository.get_by_league_and_season = AsyncMock(return_value=None)

    async def run():
        result = await service.sync_standings(FakeDatabase(), 17, 2025)
        assert result["success"] is False
        assert result["reason"] == "league_season_unresolved"
        provider.get_league_standings.assert_not_awaited()
        service.standing_repository.upsert_for_league_season.assert_not_awaited()

    asyncio.run(run())


def test_empty_provider_snapshot_does_not_delete_existing_rows():
    service, provider, _ = make_service(
        {"response": [{"league": {"standings": []}}]}
    )

    async def run():
        result = await service.sync_standings(FakeDatabase(), 17, 2025)
        assert result["success"] is False
        assert result["reason"] == "empty_snapshot"
        provider.get_league_standings.assert_awaited_once_with(812, 2025)
        service.standing_repository.upsert_for_league_season.assert_not_awaited()

    asyncio.run(run())


def test_standing_model_uses_league_season_as_its_scope():
    assert "league_season_id" in Standings.__table__.columns
    assert "league_id" not in Standings.__table__.columns
    assert "season" not in Standings.__table__.columns
    assert {"league_season_id", "team_id"} in {
        frozenset(constraint.columns.keys())
        for constraint in Standings.__table__.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }


def test_standing_response_derives_local_scope_from_league_season():
    now = datetime.now(timezone.utc)
    row = Standings(
        id=3,
        league_season=LeagueSeason(id=501, league_id=17, season="2025"),
        team_id=45,
        team_name="Example FC",
        position=1,
        points=3,
        played=1,
        won=1,
        drawn=0,
        lost=0,
        goals_for=2,
        goals_against=1,
        goal_difference=1,
        created_at=now,
        updated_at=now,
    )

    response = StandingResponse.model_validate(row)
    assert response.league_id == 17
    assert response.season == "2025"


def test_scheduler_uses_allowed_league_season_pairs():
    class FakeResult:
        def all(self):
            return [(17, "02025"), (17, "2025"), (18, "invalid")]

    class FakeDatabase:
        async def execute(self, statement):
            return FakeResult()

    async def run():
        scheduler = LiveUpdateScheduler.__new__(LiveUpdateScheduler)
        pairs = await scheduler._get_allowed_standings_pairs(FakeDatabase())
        assert pairs == [(17, 2025)]

    asyncio.run(run())