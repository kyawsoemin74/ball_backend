from typing import Optional

from app.providers.base.football_api_client import FootballAPIClient


class TeamProvider:
    """Transport-only provider for team-related API-Football calls."""

    def __init__(self, client: FootballAPIClient) -> None:
        self.client = client

    async def get_team_details(self, team_id: int) -> Optional[dict]:
        return await self.client.get("/teams", params={"id": team_id})

    async def get_team_squad(self, team_id: int) -> Optional[dict]:
        return await self.client.get("/players/squads", params={"team": team_id})

    async def get_team_statistics(self, team_id: int, league_id: int, season: int) -> Optional[dict]:
        return await self.client.get(
            "/teams/statistics",
            params={"team": team_id, "league": league_id, "season": season},
        )
