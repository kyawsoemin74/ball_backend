import logging
import time
from dataclasses import dataclass
from typing import Any, Optional

import httpx

from app.core.config import settings
from app.monitoring import PROVIDER_DURATION, PROVIDER_REQUESTS, PROVIDER_RETRIES

logger = logging.getLogger(__name__)


def _provider_error_code(payload: Any) -> str | None:
    errors = payload.get("errors") if isinstance(payload, dict) else None
    if isinstance(errors, dict):
        for value in errors.values():
            if isinstance(value, dict):
                code = value.get("code") or value.get("type")
                if code:
                    return str(code)
            elif value:
                return str(value)
    return None


def _provider_error_message(payload: Any) -> str | None:
    errors = payload.get("errors") if isinstance(payload, dict) else None
    if isinstance(errors, dict):
        values = []
        for value in errors.values():
            values.append(str(value.get("message")) if isinstance(value, dict) and value.get("message") else str(value))
        return "; ".join(values) or None
    return None


@dataclass
class FootballAPIResponse:
    payload: Any = None
    status_code: int | None = None
    error_code: str | None = None
    error_message: str | None = None
    exception_type: str | None = None


class FootballAPIClient:
    """Centralized API-Football HTTP client with shared error handling."""

    def __init__(self) -> None:
        self.base_url = settings.FOOTBALL_API_BASE_URL
        self.api_key = settings.FOOTBALL_API_KEY
        self.headers = {
            "x-apisports-key": self.api_key,
            "Content-Type": "application/json",
        }

    async def request(
        self,
        method: str,
        path: str,
        params: Optional[dict] = None,
        timeout: float = 30.0,
        retries: int = 2,
    ) -> Optional[dict]:
        if not self.api_key:
            raise ValueError("FOOTBALL_API_KEY not set in environment variables")

        endpoint = path if path.startswith("http") else f"{self.base_url}{path}"
        metric_path = path.split("?", 1)[0][:80]

        for attempt in range(retries + 1):
            started = time.perf_counter()
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    response = await client.request(method, endpoint, headers=self.headers, params=params)
                    response.raise_for_status()
                    payload = response.json()
                    PROVIDER_REQUESTS.labels(method.upper(), metric_path, "success").inc()
                    PROVIDER_DURATION.labels(method.upper(), metric_path).observe(time.perf_counter() - started)
                    return payload
            except Exception as exc:
                category = "timeout" if isinstance(exc, httpx.TimeoutException) else "provider_error"
                if isinstance(exc, httpx.HTTPStatusError):
                    category = "rate_limit" if exc.response.status_code == 429 else "http_error"
                logger.warning("Football API request failed (attempt %s/%s): %s", attempt + 1, retries + 1, exc)
                if attempt == retries:
                    PROVIDER_REQUESTS.labels(method.upper(), metric_path, category).inc()
                    PROVIDER_DURATION.labels(method.upper(), metric_path).observe(time.perf_counter() - started)
                    logger.error("Football API request failed for %s %s: %s", method.upper(), endpoint, exc)
                    return None
                PROVIDER_RETRIES.labels(method.upper(), metric_path).inc()

    async def get(self, path: str, params: Optional[dict] = None, timeout: float = 30.0) -> Optional[dict]:
        return await self.request("GET", path, params=params, timeout=timeout)

    async def request_with_metadata(
        self,
        method: str,
        path: str,
        params: Optional[dict] = None,
        timeout: float = 30.0,
        retries: int = 2,
    ) -> FootballAPIResponse:
        if not self.api_key:
            return FootballAPIResponse(
                error_code="missing_api_key",
                error_message="FOOTBALL_API_KEY not set in environment variables",
                exception_type="ValueError",
            )

        endpoint = path if path.startswith("http") else f"{self.base_url}{path}"
        metric_path = path.split("?", 1)[0][:80]
        for attempt in range(retries + 1):
            started = time.perf_counter()
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    response = await client.request(method, endpoint, headers=self.headers, params=params)
                    try:
                        payload = response.json()
                    except ValueError as exc:
                        return FootballAPIResponse(
                            status_code=response.status_code,
                            error_code="malformed_json",
                            error_message=str(exc),
                            exception_type=exc.__class__.__name__,
                        )
                    if response.status_code >= 400:
                        if attempt < retries and response.status_code >= 500:
                            PROVIDER_RETRIES.labels(method.upper(), metric_path).inc()
                            continue
                        category = "rate_limit" if response.status_code == 429 else "http_error"
                        PROVIDER_REQUESTS.labels(method.upper(), metric_path, category).inc()
                        PROVIDER_DURATION.labels(method.upper(), metric_path).observe(time.perf_counter() - started)
                        return FootballAPIResponse(
                            payload=payload,
                            status_code=response.status_code,
                            error_code=_provider_error_code(payload),
                            error_message=_provider_error_message(payload),
                        )
                    PROVIDER_REQUESTS.labels(method.upper(), metric_path, "success").inc()
                    PROVIDER_DURATION.labels(method.upper(), metric_path).observe(time.perf_counter() - started)
                    return FootballAPIResponse(
                        payload=payload,
                        status_code=response.status_code,
                        error_code=_provider_error_code(payload),
                        error_message=_provider_error_message(payload),
                    )
            except Exception as exc:
                category = "timeout" if isinstance(exc, httpx.TimeoutException) else "provider_error"
                if attempt == retries:
                    PROVIDER_REQUESTS.labels(method.upper(), metric_path, category).inc()
                    PROVIDER_DURATION.labels(method.upper(), metric_path).observe(time.perf_counter() - started)
                    return FootballAPIResponse(
                        error_message=str(exc),
                        exception_type=exc.__class__.__name__,
                    )
                PROVIDER_RETRIES.labels(method.upper(), metric_path).inc()
        return FootballAPIResponse(error_code="request_failed")

    async def get_with_metadata(
        self,
        path: str,
        params: Optional[dict] = None,
        timeout: float = 30.0,
    ) -> FootballAPIResponse:
        return await self.request_with_metadata("GET", path, params=params, timeout=timeout)

    async def post(self, path: str, params: Optional[dict] = None, timeout: float = 30.0) -> Optional[dict]:
        return await self.request("POST", path, params=params, timeout=timeout)
