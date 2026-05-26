from fastapi.testclient import TestClient

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
