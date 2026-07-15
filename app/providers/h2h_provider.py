from typing import Optional

from app.providers.base.football_api_client import FootballAPIClient


class H2HProvider:
    """Transport-only provider for H2H-related API-Football calls."""

    def __init__(self, client: FootballAPIClient) -> None:
        self.client = client

    async def get_match_h2h(self, match_id: int) -> Optional[dict]:
        return await self.client.get("/fixtures/headtohead", params={"fixture": match_id})

    async def get_h2h_by_key(self, h2h_key: str) -> Optional[dict]:
        return await self.client.get("/fixtures/headtohead", params={"h2h": h2h_key})
