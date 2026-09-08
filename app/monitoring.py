import os
import logging
import time
import uuid
from functools import wraps
from typing import Optional

from fastapi import APIRouter, Request
from prometheus_client import Counter, Gauge, Histogram, CONTENT_TYPE_LATEST, CollectorRegistry, generate_latest, start_http_server
from prometheus_client.multiprocess import MultiProcessCollector
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

logger = logging.getLogger(__name__)

# HTTP metrics
REQUEST_COUNT = Counter(
    "fover_http_requests_total",
    "Total number of HTTP requests",
    ["method", "endpoint", "http_status"],
)
REQUEST_LATENCY = Histogram(
    "fover_http_request_latency_seconds",
    "HTTP request latency in seconds",
    ["method", "endpoint"],
)
REQUEST_EXCEPTIONS = Counter(
    "fover_http_exceptions_total",
    "Total number of unhandled HTTP exceptions",
    ["method", "endpoint", "exception_type"],
)
REQUEST_IN_PROGRESS = Gauge(
    "fover_http_requests_in_progress",
    "Current number of in-flight HTTP requests",
    ["method", "endpoint"],
)

# Authentication metrics
JWT_FAILURES = Counter(
    "fover_jwt_failures_total",
    "Total number of failed JWT authentication attempts",
)

# Cache metrics
CACHE_HITS = Counter(
    "fover_cache_hits_total",
    "Total number of cache hits",
)
CACHE_MISSES = Counter(
    "fover_cache_misses_total",
    "Total number of cache misses",
)
CACHE_GET_FAILURES = Counter(
    "fover_cache_get_failures_total",
    "Total number of cache GET failures",
)
CACHE_SET_FAILURES = Counter(
    "fover_cache_set_failures_total",
    "Total number of cache SET failures",
)
CACHE_DELETE_FAILURES = Counter(
    "fover_cache_delete_failures_total",
    "Total number of cache DELETE failures",
)
CACHE_DESERIALIZE_FAILURES = Counter(
    "fover_cache_deserialize_failures_total",
    "Total number of malformed cached payload deserialization failures",
)

# External dependency health metrics
REDIS_UP = Gauge(
    "fover_redis_up",
    "Redis connectivity status (1 = up, 0 = down)",
)
POSTGRES_UP = Gauge(
    "fover_postgres_up",
    "PostgreSQL connectivity status (1 = up, 0 = down)",
)

# Scheduler metrics
SCHEDULER_UP = Gauge(
    "fover_scheduler_up",
    "Scheduler alive status (1 = up, 0 = down)",
)
SCHEDULER_JOB_RUNS = Counter(
    "fover_scheduler_job_runs_total",
    "Total scheduler job executions",
    ["job"],
)
SCHEDULER_JOB_ERRORS = Counter(
    "fover_scheduler_job_errors_total",
    "Total scheduler job failures",
    ["job"],
)

# Sync and provider observability. Labels are bounded operation/category values.
SYNC_TOTAL = Counter(
    "fover_sync_total",
    "Synchronization operations by outcome",
    ["operation", "status", "failure_category"],
)
SYNC_DURATION = Histogram(
    "fover_sync_duration_seconds",
    "Synchronization operation duration",
    ["operation"],
)
SYNC_RETRIES = Counter(
    "fover_sync_retries_total",
    "Retryable synchronization failures",
    ["operation"],
)
FINALIZATION_TOTAL = Counter(
    "fover_finalization_total",
    "Terminal match finalization outcomes",
    ["status", "failure_category"],
)
PROVIDER_REQUESTS = Counter(
    "fover_provider_requests_total",
    "External provider requests by outcome",
    ["method", "path", "status"],
)
PROVIDER_DURATION = Histogram(
    "fover_provider_request_duration_seconds",
    "External provider request duration",
    ["method", "path"],
)
PROVIDER_RETRIES = Counter(
    "fover_provider_retries_total",
    "External provider retry attempts",
    ["method", "path"],
)

metrics_router = APIRouter()


def _metrics_registry() -> Optional[CollectorRegistry]:
    multiproc_dir = os.environ.get("PROMETHEUS_MULTIPROC_DIR")
    if multiproc_dir:
        registry = CollectorRegistry()
        MultiProcessCollector(registry)
        return registry
    return None


@metrics_router.get("/metrics")
def metrics() -> Response:
    registry = _metrics_registry()
    if registry is not None:
        data = generate_latest(registry)
    else:
        data = generate_latest()
    return Response(content=data, media_type=CONTENT_TYPE_LATEST)


class MonitoringMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        endpoint = request.url.path
        method = request.method
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        request_id = request_id[:128]
        request.state.request_id = request_id
        started = time.perf_counter()
        with REQUEST_IN_PROGRESS.labels(method, endpoint).track_inprogress():
            with REQUEST_LATENCY.labels(method, endpoint).time():
                try:
                    response = await call_next(request)
                    REQUEST_COUNT.labels(method, endpoint, str(response.status_code)).inc()
                    response.headers["X-Request-ID"] = request_id
                    if endpoint not in {"/health", "/health/live", "/health/ready"}:
                        logger.info(
                            "http_request_completed",
                            extra={
                                "event": "http_request_completed",
                                "request_id": request_id,
                                "method": method,
                                "path": endpoint,
                                "status_code": response.status_code,
                                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                            },
                        )
                    return response
                except Exception as exc:
                    REQUEST_EXCEPTIONS.labels(method, endpoint, exc.__class__.__name__).inc()
                    REQUEST_COUNT.labels(method, endpoint, "500").inc()
                    logger.exception(
                        "http_request_failed",
                        extra={
                            "event": "http_request_failed",
                            "request_id": request_id,
                            "method": method,
                            "path": endpoint,
                            "status_code": 500,
                            "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                            "error_category": exc.__class__.__name__,
                        },
                    )
                    raise


def _check_redis_health() -> bool:
    try:
        from app.redis import sync_redis

        healthy = sync_redis.ping()
        REDIS_UP.set(1 if healthy else 0)
        return bool(healthy)
    except Exception:
        REDIS_UP.set(0)
        return False


async def _check_postgres_health() -> bool:
    from sqlalchemy import text
    from app.db import async_session

    try:
        async with async_session() as db:
            await db.execute(text("SELECT 1"))
        POSTGRES_UP.set(1)
        return True
    except Exception:
        POSTGRES_UP.set(0)
        return False


async def refresh_dependency_health() -> tuple[bool, bool]:
    postgres_ok = await _check_postgres_health()
    redis_ok = _check_redis_health()
    if not postgres_ok:
        POSTGRES_UP.set(0)
    if not redis_ok:
        REDIS_UP.set(0)
    return postgres_ok, redis_ok


def start_worker_metrics_server(port: int = 8001) -> None:
    start_http_server(port)


def observe_sync(operation: str):
    """Record sync outcome and duration without changing the sync contract."""
    def decorator(function):
        @wraps(function)
        async def observed(*args, **kwargs):
            started = time.perf_counter()
            status = "failure"
            failure_category = "exception"
            try:
                result = await function(*args, **kwargs)
                if isinstance(result, dict) and (result.get("success") is False or result.get("error")):
                    failure_category = str(result.get("failure_category") or result.get("reason") or "provider_failure")[:64]
                else:
                    status = "success"
                    failure_category = "none"
                return result
            except Exception as exc:
                failure_category = exc.__class__.__name__
                raise
            finally:
                SYNC_TOTAL.labels(operation, status, failure_category).inc()
                SYNC_DURATION.labels(operation).observe(time.perf_counter() - started)
                if status == "failure" and failure_category in {"PROVIDER_FAILURE", "INVALID_RESPONSE", "SYNC_UNAVAILABLE", "provider_failure", "no_data"}:
                    SYNC_RETRIES.labels(operation).inc()
                logger.log(
                    logging.INFO if status == "success" else logging.WARNING,
                    "sync_completed" if status == "success" else "sync_failed",
                    extra={
                        "event": "sync_completed" if status == "success" else "sync_failed",
                        "component": operation,
                        "status": status,
                        "failure_category": failure_category,
                        "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                    },
                )
        return observed
    return decorator
