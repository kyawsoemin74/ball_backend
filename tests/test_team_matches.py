import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.api import teams as teams_api
from app.db import get_db
from app.main import app
from app.models.match import Match
from app.repositories.match_repository import MatchRepository
from app.schemas.match import MatchResponse
from app.services.football import FootballAPIService
from app.services.match_service import MatchService
from app.cache import make_cache_key
from app.core.config import settings

client = TestClient(app)


class FakeQuery:
    def __init__(self):
        self.where_clauses = []
        self.order_by_clauses = []

    def where(self, *clauses):
        self.where_clauses.extend(clauses)
        return self

    def order_by(self, *clauses):
        self.order_by_clauses.extend(clauses)
        return self


class FakeResult:
    def __init__(self, rows):
        self._rows = list(rows)

    def scalars(self):
        return self

    def all(self):
        return self._rows

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None


class FakeDB:
    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.query = None

    async def execute(self, query):
        self.query = query
        return FakeResult(self.rows)


class RecordingCacheService:
    def __init__(self, cached=None):
        self.cached = cached
        self.get_calls = []
        self.set_calls = []

    async def get_json(self, key):
        self.get_calls.append(key)
        return self.cached

    async def set_json(self, key, payload, ttl):
        self.set_calls.append((key, payload, ttl))


class RecordingMatchRepository:
    def __init__(self, rows):
        self.rows = list(rows)
        self.calls = []

    async def get_team_matches(self, db, team_id):
        self.calls.append((db, team_id))
        return self.rows


class FakeFixtureSyncService:
    def __init__(self, match_repository=None, cache_service=None):
        self.match_repository = match_repository
        self.cache_service = cache_service
        self._upsert_leagues_from_fixtures = lambda *args, **kwargs: None


def make_match(match_id, status, match_time, home_team_id=None, away_team_id=None):
    return Match(
        match_id=match_id,
        league_id=39,
        season=2026,
        league_name="Test League",
        home_team="Home",
        away_team="Away",
        home_team_id=home_team_id,
        away_team_id=away_team_id,
        home_score=0,
        away_score=0,
        match_time=match_time,
        status=status,
        elapsed=0,
    )


def test_repository_returns_only_team_matches_in_deterministic_order(monkeypatch):
    fake_query = FakeQuery()
    monkeypatch.setattr("app.repositories.match_repository.select", lambda *args, **kwargs: fake_query)

    rows = [
        make_match(2, "FT", datetime(2026, 1, 2, tzinfo=timezone.utc), home_team_id=7, away_team_id=8),
        make_match(1, "NS", datetime(2026, 1, 1, tzinfo=timezone.utc), home_team_id=8, away_team_id=7),
    ]
    db = FakeDB(rows)
    repository = MatchRepository()

    result = asyncio.run(repository.get_team_matches(db, team_id=7))

    assert result == rows
    assert len(fake_query.where_clauses) == 1
    assert len(fake_query.order_by_clauses) == 2
    assert "match_time" in str(fake_query.order_by_clauses[0])
    assert "fixture_id" in str(fake_query.order_by_clauses[1])


def test_match_service_uses_cache_and_populates_serialized_payload_on_miss():
    cache_service = RecordingCacheService(cached=None)
    fixture_sync_service = FakeFixtureSyncService(
        match_repository=RecordingMatchRepository(rows=[
            make_match(2, "FT", datetime(2026, 1, 2, tzinfo=timezone.utc), home_team_id=7, away_team_id=8),
            make_match(1, "NS", datetime(2026, 1, 1, tzinfo=timezone.utc), home_team_id=8, away_team_id=7),
        ]),
        cache_service=cache_service,
    )
    service = MatchService(
        client=object(),
        team_service=SimpleNamespace(),
        cache_service=cache_service,
        standing_service=SimpleNamespace(),
        fixture_provider=SimpleNamespace(),
        fixture_sync_service=fixture_sync_service,
    )

    result = asyncio.run(service.get_team_matches(object(), 7))

    assert result[0]["status"] == "NS"
    assert result[1]["status"] == "FT"
    assert cache_service.get_calls == [make_cache_key("team", 7, "matches")]
    assert len(cache_service.set_calls) == 1
    assert cache_service.set_calls[0][0] == make_cache_key("team", 7, "matches")
    assert cache_service.set_calls[0][2] == settings.REDIS_TTL_TEAM_FIXTURES
    assert all(not isinstance(item, Match) for item in cache_service.set_calls[0][1])
    assert all(isinstance(item, dict) for item in cache_service.set_calls[0][1])


def test_match_service_returns_cached_payload_without_repository_call():
    cache_service = RecordingCacheService(cached=[{"match_id": 99, "status": "NS"}])
    repository = RecordingMatchRepository(rows=[])
    fixture_sync_service = FakeFixtureSyncService(match_repository=repository, cache_service=cache_service)
    service = MatchService(
        client=object(),
        team_service=SimpleNamespace(),
        cache_service=cache_service,
        standing_service=SimpleNamespace(),
        fixture_provider=SimpleNamespace(),
        fixture_sync_service=fixture_sync_service,
    )

    result = asyncio.run(service.get_team_matches(object(), 7))

    assert result == [{"match_id": 99, "status": "NS"}]
    assert repository.calls == []


def test_football_service_delegates_team_matches_to_match_service():
    captured = {}

    class StubMatchService:
        async def get_team_matches(self, db, team_id):
            captured["args"] = (db, team_id)
            return [{"match_id": team_id, "status": "NS"}]

    db = object()
    service = FootballAPIService.__new__(FootballAPIService)
    service.match_service = StubMatchService()

    result = asyncio.run(service.get_team_matches(db, 11))

    assert result == [{"match_id": 11, "status": "NS"}]
    assert captured["args"] == (db, 11)


def test_team_matches_route_uses_existing_match_response_model_and_behaviour(monkeypatch):
    route = next(route for route in teams_api.router.routes if getattr(route, "name", None) == "get_team_matches")
    assert route.response_model == list[MatchResponse]

    async def fake_get_team_matches(db, team_id):
        return [{
            "match_id": 101,
            "league_id": 39,
            "match_time": "2026-01-01T12:00:00Z",
            "status": "NS",
            "home_team": "Home",
            "away_team": "Away",
            "home_score": 0,
            "away_score": 0,
        }]

    monkeypatch.setattr(teams_api.football_service, "get_team_matches", fake_get_team_matches)

    async def override_get_db():
        yield FakeDB([SimpleNamespace(team_id=7)])

    app.dependency_overrides[get_db] = override_get_db
    try:
        response = client.get("/api/teams/7/matches")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()[0]["match_id"] == 101


def test_team_matches_route_returns_empty_list_for_existing_team(monkeypatch):
    async def fake_get_team_matches(db, team_id):
        return []

    monkeypatch.setattr(teams_api.football_service, "get_team_matches", fake_get_team_matches)

    async def override_get_db():
        yield FakeDB([SimpleNamespace(team_id=7)])

    app.dependency_overrides[get_db] = override_get_db
    try:
        response = client.get("/api/teams/7/matches")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == []


def test_team_matches_route_returns_404_for_missing_team(monkeypatch):
    async def fake_get_team_matches(db, team_id):
        return []

    monkeypatch.setattr(teams_api.football_service, "get_team_matches", fake_get_team_matches)

    async def override_get_db():
        yield FakeDB([])

    app.dependency_overrides[get_db] = override_get_db
    try:
        response = client.get("/api/teams/7/matches")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404
