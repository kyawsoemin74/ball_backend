import os
from typing import Optional

from fastapi import APIRouter, Request
from prometheus_client import Counter, Gauge, Histogram, CONTENT_TYPE_LATEST, CollectorRegistry, generate_latest, start_http_server
from prometheus_client.multiprocess import MultiProcessCollector
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

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
        with REQUEST_IN_PROGRESS.labels(method, endpoint).track_inprogress():
            with REQUEST_LATENCY.labels(method, endpoint).time():
                try:
                    response = await call_next(request)
                    REQUEST_COUNT.labels(method, endpoint, str(response.status_code)).inc()
                    return response
                except Exception as exc:
                    REQUEST_EXCEPTIONS.labels(method, endpoint, exc.__class__.__name__).inc()
                    REQUEST_COUNT.labels(method, endpoint, "500").inc()
                    raise


def start_worker_metrics_server(port: int = 8001) -> None:
    start_http_server(port)
