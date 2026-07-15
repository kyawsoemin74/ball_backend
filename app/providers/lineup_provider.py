from typing import Optional

from app.providers.base.football_api_client import FootballAPIClient


class LineupProvider:
    """Transport-only provider for lineup-related API-Football calls."""

    def __init__(self, client: FootballAPIClient) -> None:
        self.client = client

    async def get_match_lineup(self, match_id: int) -> Optional[dict]:
        return await self.client.get("/fixtures/lineups", params={"fixture": match_id})
