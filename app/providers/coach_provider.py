from app.services.base.football_client import FootballAPIClient


class CoachProvider:
    """Transport-only provider for coach data from API-Football."""

    def __init__(self, client: FootballAPIClient) -> None:
        self.client = client

    async def get_team_coach(self, team_id: int) -> dict | None:
        data = await self.client.get("/coachs", params={"team": team_id})
        if not isinstance(data, dict) or "response" not in data:
            return None

        coaches = data.get("response", [])
        if not isinstance(coaches, list) or not coaches:
            return None

        coach = coaches[0]
        return coach if isinstance(coach, dict) else None

    async def get_team_coach_payload(self, team_id: int) -> dict | None:
        coach = await self.get_team_coach(team_id)
        return {"coach": coach} if isinstance(coach, dict) else None
