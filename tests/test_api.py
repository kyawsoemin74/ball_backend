import pytest
from fastapi.testclient import TestClient
from starlette.routing import NoMatchFound

from app.db import get_db
from app.main import app
from app.schemas.match import MatchResponse

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


def test_matches_date_endpoint_uses_ordered_repository_results(monkeypatch):
    from app.api import matches as matches_api

    ordered_matches = [
        {"match_id": 2, "league_id": 2, "league_name": "Ordered League", "country_name": "Test", "match_time": "2026-06-05T18:00:00+00:00", "status": "NS", "home_team": "A", "away_team": "B", "home_score": 0, "away_score": 0},
    ]

    async def fake_get_matches_by_date(self, db, date_val):
        return []

    def fake_order_matches_for_date(matches):
        return ordered_matches

    monkeypatch.setattr(matches_api.MatchRepository, "get_matches_by_date", fake_get_matches_by_date)
    monkeypatch.setattr(matches_api.MatchRepository, "order_matches_for_date", staticmethod(fake_order_matches_for_date))

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


def test_date_matches_response_excludes_availability_flags():
    response = client.get("/api/matches/date/2026-06-10")

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


def test_has_availability_data_accepts_cached_payload_shapes():
    from app.api.matches import _has_availability_data

    assert _has_availability_data({"odds": [{"odd": 1.23}]}) is True
    assert _has_availability_data({"response": [{"team": "A"}]}) is True
    assert _has_availability_data({"source": "database", "odds": []}) is False
    assert _has_availability_data({"error": "No data"}) is False


def test_old_match_standing_route_removed():
    with pytest.raises(NoMatchFound):
        app.url_path_for("get_match_standing", match_id="1")
