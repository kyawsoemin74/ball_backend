from app.services.base.football_client import FootballAPIClient


class CoachProvider:
    """Transport-only provider for coach data from API-Football."""

    def __init__(self, client: FootballAPIClient) -> None:
        self.client = client

    async def get_team_coach_response(self, team_id: int) -> dict | None:
        return await self.client.get("/coachs", params={"team": team_id})
