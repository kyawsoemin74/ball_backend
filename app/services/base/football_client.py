import logging
from typing import Any, Optional

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


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

        for attempt in range(retries + 1):
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    response = await client.request(method, endpoint, headers=self.headers, params=params)
                    response.raise_for_status()
                    return response.json()
            except Exception as exc:
                logger.warning("Football API request failed (attempt %s/%s): %s", attempt + 1, retries + 1, exc)
                if attempt == retries:
                    logger.error("Football API request failed for %s %s: %s", method.upper(), endpoint, exc)
                    return None

    async def get(self, path: str, params: Optional[dict] = None, timeout: float = 30.0) -> Optional[dict]:
        return await self.request("GET", path, params=params, timeout=timeout)

    async def post(self, path: str, params: Optional[dict] = None, timeout: float = 30.0) -> Optional[dict]:
        return await self.request("POST", path, params=params, timeout=timeout)
