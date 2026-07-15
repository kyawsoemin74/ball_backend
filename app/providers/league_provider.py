from typing import Optional

from app.providers.base.football_api_client import FootballAPIClient


class LeagueProvider:
    """Transport-only provider for league-related API-Football calls."""

    def __init__(self, client: FootballAPIClient) -> None:
        self.client = client

    async def get_league_top_scorers(self, league_id: int, season: int) -> Optional[dict]:
        return await self.client.get("/players/topscorers", params={"league": league_id, "season": season})

    async def get_league_details(self, league_id: int) -> Optional[dict]:
        return await self.client.get("/leagues", params={"id": league_id})

    async def get_all_leagues(self) -> Optional[dict]:
        return await self.client.get("/leagues")
