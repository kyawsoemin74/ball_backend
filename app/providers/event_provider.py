from typing import Optional

from app.providers.base.football_api_client import FootballAPIClient


class EventProvider:
    """Transport-only provider for event-related API-Football calls."""

    def __init__(self, client: FootballAPIClient) -> None:
        self.client = client

    async def get_match_events(self, match_id: int) -> Optional[dict]:
        return await self.client.get("/fixtures/events", params={"fixture": match_id})
