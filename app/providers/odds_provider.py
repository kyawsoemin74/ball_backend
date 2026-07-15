from typing import Optional

from app.providers.base.football_api_client import FootballAPIClient


class OddsProvider:
    """Transport-only provider for odds-related API-Football calls."""

    def __init__(self, client: FootballAPIClient) -> None:
        self.client = client

    async def get_match_odds(self, match_id: int) -> Optional[dict]:
        return await self.client.get("/odds", params={"fixture": match_id})
