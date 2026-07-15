import asyncio
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.api import teams as teams_api
from app.db import get_db
from app.main import app
from app.repositories.match_repository import MatchRepository
from app.services.football import FootballAPIService
from app.services.match_service import MatchService
from app.cache import make_cache_key
from app.core.config import settings

# Reuse helpers from existing tests module
from tests.test_team_matches import (
    FakeDB,
    RecordingCacheService,
    make_match,
    RecordingMatchRepository,
    FakeFixtureSyncService,
    client,
)

client = TestClient(app)


class RecordingMatchRepositoryRecent:
    def __init__(self, rows):
        self.rows = list(rows)
        self.calls = []

    async def get_team_matches_recent(self, db, team_id):
        self.calls.append((db, team_id))
        return self.rows


def test_repository_get_team_matches_recent_orders_desc(monkeypatch):
    # Capture order_by clauses
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

    monkeypatch.setattr("app.repositories.match_repository.select", lambda *args, **kwargs: FakeQuery())

    rows = [
        make_match(3, "FT", datetime(2026, 1, 3, tzinfo=timezone.utc), home_team_id=7, away_team_id=8),
        make_match(2, "FT", datetime(2026, 1, 2, tzinfo=timezone.utc), home_team_id=7, away_team_id=8),
    ]
    db = FakeDB(rows)
    repository = MatchRepository()

    result = asyncio.run(repository.get_team_matches_recent(db, team_id=7))

    assert result == rows


def test_match_service_finished_cache_hit_does_not_call_repository():
    cache_service = RecordingCacheService(cached=[{"match_id": 99, "status": "FT"}])
    repository = RecordingMatchRepositoryRecent(rows=[])
    fixture_sync_service = FakeFixtureSyncService(match_repository=repository, cache_service=cache_service)
    service = MatchService(
        client=object(),
        team_service=SimpleNamespace(),
        cache_service=cache_service,
        standing_service=SimpleNamespace(),
        fixture_provider=SimpleNamespace(),
        fixture_sync_service=fixture_sync_service,
    )

    result = asyncio.run(service.get_team_finished_matches(object(), 7))

    assert result == [{"match_id": 99, "status": "FT"}]
    assert repository.calls == []


def test_match_service_finished_cache_miss_filters_top10_and_caches():
    cache_service = RecordingCacheService(cached=None)

    # Create 12 matches, newest first
    now = datetime.now(timezone.utc)
    rows = []
    for i in range(12):
        # alternate finished and non-finished
        status = "FT" if i % 1 == 0 else "NS"
        rows.append(make_match(100 + i, "FT", now - timedelta(minutes=i), home_team_id=7, away_team_id=8))

    repository = RecordingMatchRepositoryRecent(rows=rows)
    fixture_sync_service = FakeFixtureSyncService(match_repository=repository, cache_service=cache_service)
    service = MatchService(
        client=object(),
        team_service=SimpleNamespace(),
        cache_service=cache_service,
        standing_service=SimpleNamespace(),
        fixture_provider=SimpleNamespace(),
        fixture_sync_service=fixture_sync_service,
    )

    result = asyncio.run(service.get_team_finished_matches(object(), 7))

    # Repository called once
    assert repository.calls and len(repository.calls) == 1
    # Cache populated once
    assert len(cache_service.set_calls) == 1
    set_key, set_payload, set_ttl = cache_service.set_calls[0]
    assert set_key == make_cache_key("team", 7, "finished-matches")
    assert set_ttl == settings.REDIS_TTL_TEAM_FIXTURES
    # Payload is list[dict]
    assert isinstance(set_payload, list)
    assert all(isinstance(item, dict) for item in set_payload)
    # Top 10 applied
    assert len(set_payload) == 10
    # Newest first: first payload item corresponds to rows[0]
    assert set_payload[0]["match_id"] == rows[0].match_id


def test_football_service_delegates_finished_to_match_service():
    captured = {}

    class StubMatchService:
        async def get_team_finished_matches(self, db, team_id):
            captured["args"] = (db, team_id)
            return [{"match_id": team_id, "status": "FT"}]

    db = object()
    service = FootballAPIService.__new__(FootballAPIService)
    service.match_service = StubMatchService()

    result = asyncio.run(service.get_team_finished_matches(db, 11))

    assert result == [{"match_id": 11, "status": "FT"}]
    assert captured["args"] == (db, 11)


def test_team_finished_route_behaviour(monkeypatch):
    route = next(route for route in teams_api.router.routes if getattr(route, "name", None) == "get_team_finished_matches")
    from app.schemas.match import MatchResponse
    assert route.response_model == list[MatchResponse]

    async def fake_get_team_finished_matches(db, team_id):
        return [{
            "match_id": 201,
            "league_id": 39,
            "match_time": "2026-01-01T12:00:00Z",
            "status": "FT",
            "home_team": "Home",
            "away_team": "Away",
            "home_score": 1,
            "away_score": 0,
        }]

    monkeypatch.setattr(teams_api.football_service, "get_team_finished_matches", fake_get_team_finished_matches)

    async def override_get_db():
        yield FakeDB([SimpleNamespace(team_id=7)])

    app.dependency_overrides[get_db] = override_get_db
    try:
        response = client.get("/api/teams/7/finished-matches")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()[0]["match_id"] == 201


def test_team_finished_route_empty_and_missing_team(monkeypatch):
    async def fake_get_team_finished_matches(db, team_id):
        return []

    monkeypatch.setattr(teams_api.football_service, "get_team_finished_matches", fake_get_team_finished_matches)

    async def override_get_db_exists():
        yield FakeDB([SimpleNamespace(team_id=7)])

    app.dependency_overrides[get_db] = override_get_db_exists
    try:
        response = client.get("/api/teams/7/finished-matches")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == []

    async def override_get_db_missing():
        yield FakeDB([])

    app.dependency_overrides[get_db] = override_get_db_missing
    try:
        response = client.get("/api/teams/7/finished-matches")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404
    assert b"Team not found" in response.content
