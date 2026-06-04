import pytest
from fastapi.testclient import TestClient
from starlette.routing import NoMatchFound

from app.main import app

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


def test_old_match_standing_route_removed():
    with pytest.raises(NoMatchFound):
        app.url_path_for("get_match_standing", match_id="1")
