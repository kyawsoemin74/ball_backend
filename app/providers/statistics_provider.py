from typing import Optional

from app.providers.base.football_api_client import FootballAPIClient


class StatisticsProvider:
    """Transport-only provider for statistics-related API-Football calls."""

    def __init__(self, client: FootballAPIClient) -> None:
        self.client = client

    async def get_match_statistics(self, match_id: int) -> Optional[dict]:
        return await self.client.get("/fixtures/statistics", params={"fixture": match_id})
