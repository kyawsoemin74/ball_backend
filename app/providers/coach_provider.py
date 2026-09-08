from app.services.base.football_client import FootballAPIClient


class CoachProvider:
    """Transport-only provider for coach data from API-Football."""

    def __init__(self, client: FootballAPIClient) -> None:
        self.client = client

    async def get_team_coach(self, team_id: int) -> dict | None:
        selection = await self.get_team_coach_selection(team_id)
        if selection.get("status") != "VERIFIED":
            return None
        coach = selection.get("coach")
        return coach if isinstance(coach, dict) else None

    async def get_team_coach_selection(self, team_id: int) -> dict:
        data = await self.client.get("/coachs", params={"team": team_id})
        if not isinstance(data, dict) or "response" not in data:
            return {"status": "NO_DATA", "coach": None}

        coaches = data.get("response", [])
        if not isinstance(coaches, list) or not coaches:
            return {"status": "NO_DATA", "coach": None}

        valid_coaches = [coach for coach in coaches if isinstance(coach, dict)]
        if len(valid_coaches) != 1:
            return {"status": "AMBIGUOUS", "coach": valid_coaches[0] if valid_coaches else None}

        coach = valid_coaches[0]
        if not coach.get("id") or not coach.get("name"):
            return {"status": "INVALID", "coach": None}

        return {"status": "VERIFIED", "coach": coach}

    async def get_team_coach_payload(self, team_id: int) -> dict | None:
        coach = await self.get_team_coach(team_id)
        return {"coach": coach} if isinstance(coach, dict) else None
