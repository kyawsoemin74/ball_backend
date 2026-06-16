from types import SimpleNamespace
import asyncio

import pytest
from fastapi.testclient import TestClient
from starlette.routing import NoMatchFound

from datetime import datetime

from fastapi import HTTPException

from app.db import get_db
from app.main import app
from app.schemas.match import MatchResponse
from app.api.admin_leagues import router as admin_leagues_router
from app.core.security import get_current_active_admin

client = TestClient(app)


def test_health_endpoint_returns_alive():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "alive"}


def test_metrics_endpoint_is_available():
    response = client.get("/metrics")
    assert response.status_code == 200
    assert "fover_http_requests_total" in response.text


def test_league_standings_new_path_exists():
    path = app.url_path_for("get_league_standings", league_id="39", season="2026")
    assert path == "/api/leagues/39/standing/2026"


def test_league_sync_route_exists():
    path = app.url_path_for("sync_all_leagues")
    assert path == "/api/leagues/sync"


def test_grouped_leagues_route_exists():
    path = app.url_path_for("get_grouped_leagues")
    assert path == "/api/leagues/grouped"


def test_admin_league_patch_route_exists():
    assert "patch_admin_league" in [route.name for route in admin_leagues_router.routes]
    path = app.url_path_for("patch_admin_league", league_id="7")
    assert path == "/api/admin/leagues/7"


def test_admin_league_patch_route_is_in_openapi():
    response = client.get("/api/openapi.json")

    assert response.status_code == 200
    assert "/api/admin/leagues/{league_id}" in response.json()["paths"]


def test_admin_league_patch_updates_league(monkeypatch):
    league = SimpleNamespace(
        league_id=7,
        name="Test League",
        country="England",
        country_code=None,
        logo=None,
        season="2024",
        display_order=999,
        is_featured=False,
        created_at=datetime.utcnow(),
        updated_at=None,
    )
    committed = {"value": False}

    class FakeResult:
        def scalar_one_or_none(self):
            return league

    class FakeDB:
        async def execute(self, query):
            return FakeResult()

        async def commit(self):
            committed["value"] = True

        async def refresh(self, obj):
            return None

    async def override_get_db():
        yield FakeDB()

    async def override_admin():
        return SimpleNamespace(role="admin", is_active=True)

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_active_admin] = override_admin
    try:
        response = client.patch("/api/admin/leagues/7", json={"display_order": 25, "is_featured": True})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert league.display_order == 25
    assert league.is_featured is True
    assert committed["value"] is True


def test_admin_league_patch_rejects_non_admin():
    async def override_admin():
        raise HTTPException(status_code=403, detail="Forbidden")

    app.dependency_overrides[get_current_active_admin] = override_admin
    try:
        response = client.patch("/api/admin/leagues/7", json={"display_order": 25})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 403


def test_team_fixtures_service_uses_db_cache_not_api_football():
    import asyncio

    from app.services.team_service import TeamService

    class FakeCacheService:
        def __init__(self):
            self.cached = None
            self.saved = None

        async def get_json(self, key):
            return self.cached

        async def set_json(self, key, payload, ttl):
            self.saved = (key, payload, ttl)

    class FakeClient:
        async def get(self, *args, **kwargs):
            raise AssertionError("API-Football should not be used for team fixtures")

    class FakeResult:
        def __init__(self, rows):
            self._rows = rows

        def scalars(self):
            return self

        def all(self):
            return list(self._rows)

    class FakeSession:
        def __init__(self, recent_rows, upcoming_rows):
            self._recent_rows = recent_rows
            self._upcoming_rows = upcoming_rows
            self.calls = 0

        async def execute(self, query):
            self.calls += 1
            rows = self._recent_rows if self.calls == 1 else self._upcoming_rows
            return FakeResult(rows)

    async def run_test():
        service = TeamService(client=FakeClient(), cache_service=FakeCacheService())
        recent = [SimpleNamespace(
            match_id=101,
            league_id=39,
            league_name="Premier League",
            match_time="2026-06-10T18:00:00+00:00",
            status="FT",
            home_team="A",
            home_team_id=40,
            away_team="B",
            away_team_id=41,
            home_score=2,
            away_score=1,
        )]
        upcoming = [SimpleNamespace(
            match_id=102,
            league_id=39,
            league_name="Premier League",
            match_time="2026-06-15T18:00:00+00:00",
            status="NS",
            home_team="A",
            home_team_id=40,
            away_team="B",
            away_team_id=41,
            home_score=0,
            away_score=0,
        )]

        payload = await service.get_cached_team_fixtures(FakeSession(recent, upcoming), team_id=40)

        assert payload["team_id"] == 40
        assert payload["recent"][0]["result"] == "W"
        assert payload["upcoming"][0]["status"] == "NS"

    asyncio.run(run_test())


def test_team_fixtures_route_exists():
    path = app.url_path_for("get_team_fixtures", team_id="1")
    assert path == "/api/teams/1/fixtures"


def test_team_squad_route_exists():
    path = app.url_path_for("get_team_squad", team_id="1")
    assert path == "/api/teams/1/squad"


def test_team_statistics_route_exists():
    path = app.url_path_for("get_team_statistics", team_id="1", league_id="39", season="2024")
    assert path == "/api/teams/1/statistics/39/2024"


def test_team_statistics_normalization_uses_total_and_average_fields():
    import asyncio

    from app.services.team_service import TeamService

    captured = {}

    class FakeClient:
        async def get(self, path, params=None):
            return {
                "response": [
                    {
                        "games": {"played": 38, "wins": 24, "draws": 8, "loses": 6},
                        "goals": {
                            "for": {
                                "total": {"total": 78},
                                "average": {"total": "2.05"},
                            },
                            "against": {
                                "total": {"total": 32},
                                "average": {"total": "0.84"},
                            },
                        },
                        "clean_sheet": {"home": 12},
                        "failed_to_score": {"home": 4},
                    }
                ]
            }

    class FakeCacheService:
        async def get_json(self, key):
            return None

        async def set_json(self, key, payload, ttl):
            captured["payload"] = payload

    async def run_test():
        service = TeamService(client=FakeClient(), cache_service=FakeCacheService())
        result = await service.get_cached_team_statistics(team_id=25, league_id=39, season=2026)
        captured["result"] = result

    asyncio.run(run_test())

    assert captured["result"]["goals_for"] == 78
    assert captured["result"]["goals_against"] == 32
    assert captured["result"]["average_goals_scored"] == 2.05
    assert captured["result"]["average_goals_conceded"] == 0.84
    assert isinstance(captured["result"]["goals_for"], int)
    assert isinstance(captured["result"]["average_goals_scored"], float)
    assert isinstance(captured["result"]["average_goals_conceded"], float)


def test_team_statistics_service_uses_league_parameter(monkeypatch):
    import asyncio

    from app.services.team_service import TeamService

    captured = {}

    class FakeClient:
        async def get(self, path, params=None):
            captured["path"] = path
            captured["params"] = params
            return {"response": [{"games": {}, "goals": {}}]}

    class FakeCacheService:
        async def get_json(self, key):
            return None

        async def set_json(self, key, payload, ttl):
            captured["cache_key"] = key
            captured["cache_payload"] = payload
            captured["ttl"] = ttl

    async def run_test():
        service = TeamService(client=FakeClient(), cache_service=FakeCacheService())
        await service.get_cached_team_statistics(team_id=25, league_id=39, season=2026)

    asyncio.run(run_test())

    assert captured["path"] == "/teams/statistics"
    assert captured["params"] == {"team": 25, "league": 39, "season": 2026}
    assert captured["cache_key"].endswith("team:25:statistics:39:2026")


def test_league_top_scorers_route_exists():
    path = app.url_path_for("get_league_top_scorers", league_id="39", season="2024")
    assert path == "/api/leagues/39/topscorers/2024"


def test_home_route_exists():
    path = app.url_path_for("get_home")
    assert path == "/api/home"


def test_home_endpoint_returns_payload(monkeypatch):
    expected = {
        "live_today": [{"league_id": 1, "name": "Live League", "country": "England", "logo": None, "season": "2024", "is_featured": False, "display_order": 1}],
        "featured": [{"league_id": 2, "name": "Featured League", "country": "Spain", "logo": None, "season": "2024", "is_featured": True, "display_order": 2}],
        "countries": [{"type": "country", "country": "England", "leagues": []}],
    }

    async def fake_get_home_payload(self, db):
        return expected

    monkeypatch.setattr("app.api.home.HomeService.get_home_payload", fake_get_home_payload)

    response = client.get("/api/home")

    assert response.status_code == 200
    assert response.json() == expected


def test_matches_date_endpoint_returns_all_matches(monkeypatch):
    from app.api import matches as matches_api

    visible_match = SimpleNamespace(
        match_id=1,
        league_id=1,
        league_name="Visible League",
        country_name="Test",
        match_time="2026-06-05T18:00:00+00:00",
        status="NS",
        home_team="A",
        away_team="B",
        home_score=0,
        away_score=0,
        league_obj=SimpleNamespace(display_order=10, is_featured=False, country="Test", name="Visible League"),
    )
    hidden_match = SimpleNamespace(
        match_id=2,
        league_id=2,
        league_name="Hidden League",
        country_name="Test",
        match_time="2026-06-05T19:00:00+00:00",
        status="NS",
        home_team="C",
        away_team="D",
        home_score=0,
        away_score=0,
        league_obj=SimpleNamespace(display_order=201, is_featured=False, country="Test", name="Hidden League"),
    )

    async def fake_get_matches_by_date(self, db, date_val, allowed_ids=None):
        assert allowed_ids == {1}
        return [hidden_match, visible_match]

    async def fake_allowed_ids(self, db):
        return {1}

    monkeypatch.setattr(matches_api.MatchRepository, "get_matches_by_date", fake_get_matches_by_date)
    monkeypatch.setattr(matches_api.AllowedLeagueRepository, "get_allowed_ids", fake_allowed_ids)

    async def override_get_db():
        yield object()

    app.dependency_overrides[get_db] = override_get_db
    try:
        response = client.get("/api/matches/date/2026-06-05")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert [item["match_id"] for item in payload] == [1, 2]


def test_matches_date_endpoint_uses_ordered_repository_results(monkeypatch):
    from app.api import matches as matches_api

    ordered_matches = [
        {"match_id": 2, "league_id": 2, "league_name": "Ordered League", "country_name": "Test", "match_time": "2026-06-05T18:00:00+00:00", "status": "NS", "home_team": "A", "away_team": "B", "home_score": 0, "away_score": 0},
    ]

    async def fake_get_matches_by_date(self, db, date_val, allowed_ids=None):
        return []

    def fake_order_matches_for_date(matches):
        return ordered_matches

    async def fake_allowed_ids(self, db):
        return {2}

    monkeypatch.setattr(matches_api.MatchRepository, "get_matches_by_date", fake_get_matches_by_date)
    monkeypatch.setattr(matches_api.MatchRepository, "order_matches_for_date", staticmethod(fake_order_matches_for_date))
    monkeypatch.setattr(matches_api.AllowedLeagueRepository, "get_allowed_ids", fake_allowed_ids)

    async def override_get_db():
        yield object()

    app.dependency_overrides[get_db] = override_get_db
    try:
        response = client.get("/api/matches/date/2026-06-05")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert payload[0]["match_id"] == 2
    assert payload[0]["league_name"] == "Ordered League"


def test_live_matches_endpoint_returns_only_allowed_league(monkeypatch):
    from app.api import matches as matches_api

    visible_match = SimpleNamespace(match_id=1, league_id=2, league_name="Allowed League", country_name="Test", match_time="2026-06-16T18:00:00+00:00", status="LIVE", home_team="A", away_team="B", home_score=1, away_score=0)
    hidden_match = SimpleNamespace(match_id=2, league_id=39, league_name="Hidden League", country_name="Test", match_time="2026-06-16T19:00:00+00:00", status="LIVE", home_team="C", away_team="D", home_score=0, away_score=0)

    async def fake_get_live_matches(self, db, allowed_ids=None):
        assert allowed_ids == {2}
        return [visible_match]

    async def fake_allowed_ids(self, db):
        return {2}

    async def fake_cache_get_json(key):
        return None

    monkeypatch.setattr(matches_api, "cache_get_json", fake_cache_get_json)
    monkeypatch.setattr(matches_api.MatchRepository, "get_live_matches", fake_get_live_matches)
    monkeypatch.setattr(matches_api.AllowedLeagueRepository, "get_allowed_ids", fake_allowed_ids)

    async def override_get_db():
        yield object()

    app.dependency_overrides[get_db] = override_get_db
    try:
        response = client.get("/api/matches/live_all")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert payload[0]["league_id"] == 2


def test_matches_all_endpoint_filters_to_allowed_league(monkeypatch):
    from app.api import matches as matches_api

    visible_match = SimpleNamespace(match_id=1, league_id=2, league_name="Allowed League", country_name="Test", match_time="2026-06-16T18:00:00+00:00", status="NS", home_team="A", away_team="B", home_score=0, away_score=0)

    async def fake_get_all_matches(self, db, allowed_ids=None, status=None, league_id=None, skip=0, limit=100):
        assert allowed_ids == {2}
        assert status == "NS"
        return [visible_match]

    async def fake_allowed_ids(self, db):
        return {2}

    monkeypatch.setattr(matches_api.MatchRepository, "get_all_matches", fake_get_all_matches)
    monkeypatch.setattr(matches_api.AllowedLeagueRepository, "get_allowed_ids", fake_allowed_ids)

    async def override_get_db():
        yield object()

    app.dependency_overrides[get_db] = override_get_db
    try:
        response = client.get("/api/matches/?status=NS")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert payload[0]["league_id"] == 2


def test_leagues_grouped_only_allowed_leagues(monkeypatch):
    from app.api import leagues as leagues_api

    league_allowed = SimpleNamespace(league_id=2, name="Allowed League", country="Spain", country_code=None, logo=None, season="2024", is_featured=False, display_order=1)
    league_hidden = SimpleNamespace(league_id=39, name="Hidden League", country="England", country_code=None, logo=None, season="2024", is_featured=False, display_order=2)

    async def fake_allowed_ids(self, db):
        return {2}

    async def fake_get_all_leagues(self, db, allowed_ids=None):
        assert allowed_ids == {2}
        return [league for league in [league_allowed, league_hidden] if league.league_id in allowed_ids]

    async def fake_cache_get_json(key):
        return None

    async def fake_cache_set_json(key, payload, ttl):
        return None

    monkeypatch.setattr(leagues_api, "cache_get_json", fake_cache_get_json)
    monkeypatch.setattr(leagues_api, "cache_set_json", fake_cache_set_json)
    monkeypatch.setattr(leagues_api.AllowedLeagueRepository, "get_allowed_ids", fake_allowed_ids)
    monkeypatch.setattr(leagues_api.LeagueRepository, "get_all_leagues", fake_get_all_leagues)

    async def override_get_db():
        yield object()

    app.dependency_overrides[get_db] = override_get_db
    try:
        response = client.get("/api/leagues/grouped")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert payload[0]["leagues"][0]["league_id"] == 2


def test_league_detail_disallowed_returns_404(monkeypatch):
    from app.api import leagues as leagues_api

    async def fake_allowed_ids(self, db):
        return {2}

    async def fake_get_by_id(self, db, league_id, allowed_ids=None):
        assert league_id == 39
        assert allowed_ids == {2}
        return None

    async def fake_cache_get_json(key):
        return None

    monkeypatch.setattr(leagues_api, "cache_get_json", fake_cache_get_json)
    monkeypatch.setattr(leagues_api.AllowedLeagueRepository, "get_allowed_ids", fake_allowed_ids)
    monkeypatch.setattr(leagues_api.LeagueRepository, "get_by_id", fake_get_by_id)

    async def override_get_db():
        yield object()

    app.dependency_overrides[get_db] = override_get_db
    try:
        response = client.get("/api/leagues/39")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404


def test_league_standings_disallowed_returns_404(monkeypatch):
    from app.api import leagues as leagues_api

    async def fake_allowed_ids(self, db):
        return {2}

    async def fake_get_cached_standings(db, league_id, season):
        return [{"position": 1}]

    monkeypatch.setattr(leagues_api.AllowedLeagueRepository, "get_allowed_ids", fake_allowed_ids)
    monkeypatch.setattr(leagues_api.football_service, "get_cached_standings", fake_get_cached_standings)

    async def override_get_db():
        yield object()

    app.dependency_overrides[get_db] = override_get_db
    try:
        response = client.get("/api/leagues/39/standing/2025")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404


def test_home_service_filters_allowed_leagues(monkeypatch):
    from app.services.home_service import HomeService

    captured = {}
    league_allowed = SimpleNamespace(league_id=2, name="Allowed League", country="Spain", country_code=None, logo=None, season="2024", is_featured=False, display_order=1)
    league_hidden = SimpleNamespace(league_id=39, name="Hidden League", country="England", country_code=None, logo=None, season="2024", is_featured=False, display_order=2)

    async def fake_allowed_ids(self, db):
        return {2}

    async def fake_leagues_with_matches_today(self, db, allowed_ids=None):
        captured['live_today_allowed_ids'] = allowed_ids
        return [league_allowed, league_hidden]

    async def fake_featured_leagues(self, db, allowed_ids=None):
        captured['featured_allowed_ids'] = allowed_ids
        return [league_allowed]

    async def fake_all_leagues(self, db, allowed_ids=None):
        captured['all_allowed_ids'] = allowed_ids
        assert allowed_ids == {2}
        return [league_allowed]

    monkeypatch.setattr("app.services.home_service.AllowedLeagueRepository.get_allowed_ids", fake_allowed_ids)
    monkeypatch.setattr("app.services.home_service.LeagueRepository.get_leagues_with_matches_today", fake_leagues_with_matches_today)
    monkeypatch.setattr("app.services.home_service.LeagueRepository.get_featured_leagues", fake_featured_leagues)
    monkeypatch.setattr("app.services.home_service.LeagueRepository.get_all_leagues", fake_all_leagues)

    payload = asyncio.run(HomeService().get_home_payload(None))

    assert payload["live_today"][0]["league_id"] == 2
    assert payload["featured"][0]["league_id"] == 2
    assert payload["countries"][0]["leagues"][0]["league_id"] == 2
    assert captured["live_today_allowed_ids"] == {2}
    assert captured["featured_allowed_ids"] == {2}
    assert captured["all_allowed_ids"] == {2}


def test_date_matches_response_excludes_availability_flags(monkeypatch):
    from app.api import matches as matches_api

    visible_match = SimpleNamespace(
        match_id=1,
        league_id=2,
        league_name="Visible League",
        country_name="Test",
        match_time="2026-06-10T18:00:00+00:00",
        status="NS",
        home_team="A",
        away_team="B",
        home_score=0,
        away_score=0,
        league_obj=SimpleNamespace(display_order=10, is_featured=False, country="Test", name="Visible League"),
    )

    async def fake_get_matches_by_date(self, db, date_val, allowed_ids=None):
        assert allowed_ids == {2}
        return [visible_match]

    async def fake_allowed_ids(self, db):
        return {2}

    monkeypatch.setattr(matches_api.MatchRepository, "get_matches_by_date", fake_get_matches_by_date)
    monkeypatch.setattr(matches_api.AllowedLeagueRepository, "get_allowed_ids", fake_allowed_ids)

    async def override_get_db():
        yield object()

    app.dependency_overrides[get_db] = override_get_db
    try:
        response = client.get("/api/matches/date/2026-06-10")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert isinstance(response.json(), list)
    assert response.json()

    first_match = response.json()[0]
    assert "has_events" not in first_match
    assert "has_stats" not in first_match
    assert "has_lineups" not in first_match
    assert "has_odds" not in first_match
    assert "has_h2h" not in first_match
    assert "has_standings" not in first_match
    assert "has_predictions" not in first_match
    assert "has_rankings" not in first_match
    assert "has_news" not in first_match
    assert "has_highlights" not in first_match
    assert "has_comments" not in first_match
    assert "is_knockout" not in first_match
    assert "has_bracket" not in first_match


def test_match_response_includes_availability_flags():
    payload = MatchResponse(
        match_id=1,
        league_id=39,
        league_name="Premier League",
        league_logo=None,
        country_name="England",
        country_logo=None,
        match_time="2026-06-10T18:00:00+00:00",
        status="FT",
        elapsed=90,
        home_team="Team A",
        home_team_id=1,
        home_team_logo=None,
        away_team="Team B",
        away_team_id=2,
        away_team_logo=None,
        home_score=2,
        away_score=1,
        venue_name=None,
        venue_city=None,
        created_at=None,
        updated_at=None,
    ).model_dump()

    assert payload["has_events"] is False
    assert payload["has_stats"] is False
    assert payload["has_lineups"] is False
    assert payload["has_odds"] is False
    assert payload["has_h2h"] is False
    assert payload["has_standings"] is False
    assert payload["has_predictions"] is False
    assert payload["has_rankings"] is False
    assert payload["has_news"] is False
    assert payload["has_highlights"] is False
    assert payload["has_comments"] is False
    assert payload["is_knockout"] is False
    assert payload["has_bracket"] is False


def test_finished_match_availability_flags_persist_with_stored_data(monkeypatch):
    from app.api.matches import _assert_match_allowed

    class FakeResult:
        def __init__(self, value):
            self._value = value

        def scalar_one_or_none(self):
            return self._value

    class FakeDB:
        def __init__(self):
            self.calls = 0
            self.league = SimpleNamespace(season=2026)

        async def execute(self, query):
            self.calls += 1
            if self.calls == 1:
                return FakeResult(None)
            if self.calls == 2:
                return FakeResult(SimpleNamespace(match_id=1))
            if self.calls == 3:
                return FakeResult(SimpleNamespace(id=1))
            if self.calls == 4:
                return FakeResult(SimpleNamespace(id=1))
            if self.calls == 5:
                return FakeResult(SimpleNamespace(id=1))
            return FakeResult(None)

        async def get(self, model, pk):
            return self.league

    async def fake_assert_match_allowed(match_id, db):
        return SimpleNamespace(
            match_id=match_id,
            league_id=39,
            league_name="Premier League",
            country_name="England",
            match_time="2026-06-10T18:00:00+00:00",
            status="FT",
            elapsed=90,
            home_team="Team A",
            home_team_id=1,
            home_team_logo=None,
            away_team="Team B",
            away_team_id=2,
            away_team_logo=None,
            home_score=2,
            away_score=1,
            venue_name=None,
            venue_city=None,
            created_at=None,
            updated_at=None,
        )

    async def fake_get_cached_statistics(db, match_id):
        return {"error": "Statistics not found"}

    async def override_get_db():
        yield FakeDB()

    monkeypatch.setattr("app.api.matches._assert_match_allowed", fake_assert_match_allowed)
    monkeypatch.setattr("app.services.football.football_service.get_cached_statistics", fake_get_cached_statistics)
    app.dependency_overrides[get_db] = override_get_db
    try:
        response = client.get("/api/matches/1")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "FT"
    assert payload["has_lineups"] is True
    assert payload["has_odds"] is True
    assert payload["has_h2h"] is True
    assert payload["has_standings"] is True


def test_has_availability_data_accepts_cached_payload_shapes():
    from app.api.matches import _has_availability_data

    assert _has_availability_data({"odds": [{"odd": 1.23}]}) is True
    assert _has_availability_data({"response": [{"team": "A"}]}) is True
    assert _has_availability_data({"source": "database", "odds": []}) is False
    assert _has_availability_data({"error": "No data"}) is False


def test_match_statistics_route_exists():
    path = app.url_path_for("get_match_statistics", match_id="1")
    assert path == "/api/matches/1/statistics"


def test_statistics_normalization_uses_team_ids_not_response_order():
    from app.services.statistics_service import StatisticsService

    service = StatisticsService(client=None)
    payload = {
        "response": [
            {
                "team": {"id": 200, "name": "Away Team"},
                "statistics": [{"type": "Ball Possession", "value": "38%"}],
            },
            {
                "team": {"id": 100, "name": "Home Team"},
                "statistics": [{"type": "Ball Possession", "value": "62%"}],
            },
        ]
    }

    normalized = service._normalize_statistics_payload(
        payload,
        match_id=1492286,
        home_team_id=100,
        away_team_id=200,
    )

    assert normalized["statistics"][0]["home_value"] == "62%"
    assert normalized["statistics"][0]["away_value"] == "38%"


def test_match_odds_endpoint_commits_db_session(monkeypatch):
    class FakeSession:
        def __init__(self):
            self.committed = False

        async def commit(self):
            self.committed = True

    fake_db = FakeSession()

    async def fake_get_cached_odds(db, match_id):
        assert db is fake_db
        return {"source": "api", "odds": [], "updated": 1}

    async def fake_assert_match_allowed(match_id, db):
        return SimpleNamespace(match_id=match_id, league_id=2)

    monkeypatch.setattr("app.services.football.football_service.get_cached_odds", fake_get_cached_odds)
    monkeypatch.setattr("app.api.matches._assert_match_allowed", fake_assert_match_allowed)

    async def override_get_db():
        yield fake_db

    app.dependency_overrides[get_db] = override_get_db
    try:
        response = client.get("/api/matches/1/odds")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert fake_db.committed is True


def test_old_match_standing_route_removed():
    with pytest.raises(NoMatchFound):
        app.url_path_for("get_match_standing", match_id="1")
