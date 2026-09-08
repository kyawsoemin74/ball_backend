import asyncio
import logging

from fastapi.testclient import TestClient
from prometheus_client import generate_latest

from app.main import app
from app.monitoring import PROVIDER_REQUESTS, POSTGRES_UP, REDIS_UP, SYNC_TOTAL, observe_sync, refresh_dependency_health


def test_request_id_is_returned_and_logged(caplog):
    client = TestClient(app)
    with caplog.at_level(logging.INFO):
        response = client.get("/", headers={"X-Request-ID": "phase7-test"})

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "phase7-test"
    record = next(item for item in caplog.records if item.message == "http_request_completed")
    assert record.request_id == "phase7-test"
    assert record.status_code == 200
    assert record.duration_ms >= 0


def test_health_checks_are_not_logged_as_request_noise(caplog):
    client = TestClient(app)
    with caplog.at_level(logging.INFO):
        response = client.get("/health/live")

    assert response.status_code == 200
    assert not any(item.message == "http_request_completed" for item in caplog.records)


def test_sync_observer_records_success_and_failure_without_resource_labels(caplog):
    @observe_sync("observability_test")
    async def successful_sync():
        return {"success": True}

    @observe_sync("observability_test")
    async def failed_sync():
        return {"success": False, "reason": "provider_failure", "match_id": 123}

    with caplog.at_level(logging.INFO):
        asyncio.run(successful_sync())
        asyncio.run(failed_sync())

    payload = generate_latest().decode()
    assert 'fover_sync_total{failure_category="none",operation="observability_test",status="success"}' in payload
    assert 'fover_sync_total{failure_category="provider_failure",operation="observability_test",status="failure"}' in payload
    assert 'match_id="123"' not in payload
    assert any(item.message == "sync_failed" and item.component == "observability_test" for item in caplog.records)


def test_provider_metric_labels_are_bounded_and_do_not_contain_credentials():
    PROVIDER_REQUESTS.labels("GET", "/fixtures", "success").inc()
    payload = generate_latest().decode()
    assert 'fover_provider_requests_total{method="GET",path="/fixtures",status="success"}' in payload
    assert "password" not in payload.lower()
    assert "jwt_secret" not in payload.lower()


def test_worker_and_scheduler_metrics_exist():
    payload = generate_latest().decode()
    assert "fover_scheduler_up" in payload
    assert "fover_scheduler_job_runs_total" in payload


def test_worker_dependency_metrics_refresh_to_healthy_values():
    postgres_ok, redis_ok = asyncio.run(refresh_dependency_health())

    assert postgres_ok is True
    assert redis_ok is True
    assert float(POSTGRES_UP._value.get()) == 1.0
    assert float(REDIS_UP._value.get()) == 1.0


def test_worker_dependency_metrics_update_on_failure(monkeypatch):
    async def fake_postgres_check():
        return False

    def fake_redis_check():
        return False

    monkeypatch.setattr("app.monitoring._check_postgres_health", fake_postgres_check)
    monkeypatch.setattr("app.monitoring._check_redis_health", fake_redis_check)

    POSTGRES_UP.set(1)
    REDIS_UP.set(1)
    asyncio.run(refresh_dependency_health())
    assert float(POSTGRES_UP._value.get()) == 0.0
    assert float(REDIS_UP._value.get()) == 0.0
