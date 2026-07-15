import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.api import teams as teams_api
from app.db import get_db
from app.main import app
from app.services import standing_service as standing_service_module
from app.services.football import football_service
from app.services.standing_service import StandingService

client = TestClient(app)


class RecordingCacheService:
    def __init__(self, cached=None):
        self.cached = cached
        self.set_calls = []

    async def get_json(self, key):
        return self.cached

    async def set_json(self, key, payload, ttl):
        self.set_calls.append((key, payload, ttl))


class RecordingStandingRepository:
    def __init__(self, rows):
        self.rows = list(rows)
        self.calls = []

    async def get_for_league_season(self, db, league_id, season):
        self.calls.append((league_id, str(season)))
        return list(self.rows)


class FakeTeamRepository:
    def __init__(self, team):
        self.team = team
        self.calls = []

    async def get_by_id(self, db, team_id):
        self.calls.append(team_id)
        return self.team


def make_standing_row(team_id=7, position=1):
    timestamp = datetime(2026, 6, 23, tzinfo=timezone.utc)
    return SimpleNamespace(
        id=position,
        league_id=39,
        season="2026",
        team_id=team_id,
        team_name=f"Team {team_id}",
        team_logo=None,
        group_name=None,
        form=None,
        description=None,
        position=position,
        points=10,
        played=5,
        won=3,
        drawn=1,
        lost=1,
        goals_for=8,
        goals_against=3,
        goal_difference=5,
        created_at=timestamp,
        updated_at=timestamp,
    )


def test_team_profile_standings_service_uses_cache_on_hit(monkeypatch):
    cached_payload = [{"position": 1, "team_id": 7}]
    cache_service = RecordingCacheService(cached=cached_payload)
    service = StandingService(client=object(), team_service=SimpleNamespace(), cache_service=cache_service, standing_provider=SimpleNamespace())

    class FailingStandingRepository:
        async def get_for_league_season(self, *args, **kwargs):
            raise AssertionError("repository should not be called on cache hit")

    monkeypatch.setattr(standing_service_module, "TeamRepository", lambda: FakeTeamRepository(SimpleNamespace(current_league_id=39, current_season="2026")))
    service.standing_repository = FailingStandingRepository()

    result = asyncio.run(service.get_team_profile_standings(object(), 7))

    assert result == cached_payload


def test_team_profile_standings_service_reads_db_and_populates_cache_on_miss(monkeypatch):
    cache_service = RecordingCacheService(cached=None)
    service = StandingService(client=object(), team_service=SimpleNamespace(), cache_service=cache_service, standing_provider=SimpleNamespace())
    repository = RecordingStandingRepository(rows=[make_standing_row(team_id=7, position=1)])
    service.standing_repository = repository
    monkeypatch.setattr(standing_service_module, "TeamRepository", lambda: FakeTeamRepository(SimpleNamespace(current_league_id=39, current_season="2026")))

    result = asyncio.run(service.get_team_profile_standings(object(), 7))

    assert result is not None
    assert repository.calls == [(39, "2026")]
    assert len(cache_service.set_calls) == 1


def test_team_profile_standings_service_returns_none_when_context_missing(monkeypatch):
    cache_service = RecordingCacheService(cached=None)
    service = StandingService(client=object(), team_service=SimpleNamespace(), cache_service=cache_service, standing_provider=SimpleNamespace())

    class FailingStandingRepository:
        async def get_for_league_season(self, *args, **kwargs):
            raise AssertionError("repository should not be called when context is missing")

    service.standing_repository = FailingStandingRepository()
    monkeypatch.setattr(standing_service_module, "TeamRepository", lambda: FakeTeamRepository(SimpleNamespace(current_league_id=None, current_season="2026")))

    result = asyncio.run(service.get_team_profile_standings(object(), 7))

    assert result is None


def test_team_profile_standings_api_route_returns_200(monkeypatch):
    async def fake_get_team_profile_standings(db, team_id):
        return [{
            "id": 1,
            "league_id": 39,
            "season": "2026",
            "position": 1,
            "team_id": team_id,
            "team_name": f"Team {team_id}",
            "team_logo": None,
            "group_name": None,
            "form": None,
            "description": None,
            "points": 10,
            "played": 5,
            "won": 3,
            "drawn": 1,
            "lost": 1,
            "goals_for": 8,
            "goals_against": 3,
            "goal_difference": 5,
            "created_at": "2026-06-23T00:00:00Z",
            "updated_at": "2026-06-23T00:00:00Z",
        }]

    monkeypatch.setattr(teams_api.football_service, "get_team_profile_standings", fake_get_team_profile_standings)

    async def override_get_db():
        yield object()

    app.dependency_overrides[get_db] = override_get_db
    try:
        response = client.get("/api/teams/7/standings")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()[0]["team_id"] == 7


def test_team_profile_standings_api_route_returns_404_when_missing(monkeypatch):
    async def fake_get_team_profile_standings(db, team_id):
        return None

    monkeypatch.setattr(teams_api.football_service, "get_team_profile_standings", fake_get_team_profile_standings)

    async def override_get_db():
        yield object()

    app.dependency_overrides[get_db] = override_get_db
    try:
        response = client.get("/api/teams/7/standings")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404


def test_football_service_delegates_to_standing_service(monkeypatch):
    captured = {}

    async def fake_get_team_profile_standings(db, team_id):
        captured["team_id"] = team_id
        return [{"team_id": team_id}]

    monkeypatch.setattr(football_service.standing_service, "get_team_profile_standings", fake_get_team_profile_standings)

    result = asyncio.run(football_service.get_team_profile_standings(object(), 11))

    assert result == [{"team_id": 11}]
    assert captured["team_id"] == 11
