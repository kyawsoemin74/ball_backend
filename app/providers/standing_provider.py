from typing import Optional

from app.providers.base.football_api_client import FootballAPIClient


class StandingProvider:
    """Transport-only provider for standings-related API-Football calls."""

    def __init__(self, client: FootballAPIClient) -> None:
        self.client = client

    async def get_league_standings(self, league_id: int, season: int) -> Optional[dict]:
        return await self.client.get("/standings", params={"league": league_id, "season": season})
