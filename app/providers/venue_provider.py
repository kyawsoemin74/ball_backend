"""
Venue Provider - Transport-only API communication.

Responsibilities:
  - Call API-Football endpoints for venue data
  - Parse venue payloads from fixture/team endpoints
  - Never write database or call Repository

Venue data sources:
  1. /fixtures endpoint returns fixture.venue = { id, name, city }
  2. /teams endpoint returns team.venue = { id, name, address, city, capacity, surface, image }
"""

from app.services.base.football_client import FootballAPIClient


class VenueProvider:
    """Provider for venue data from API-Football."""

    def __init__(self, client: FootballAPIClient) -> None:
        self.client = client

    async def get_fixture_venue(self, fixture_id: int) -> dict | None:
        """
        Get venue data from fixture endpoint.
        
        Returns fixture.venue object if exists, else None.
        """
        data = await self.client.get("/fixtures", params={"id": fixture_id})
        
        if not isinstance(data, dict) or "response" not in data:
            return None
        
        fixtures = data.get("response", [])
        if not isinstance(fixtures, list) or not fixtures:
            return None
        
        fixture = fixtures[0]
        if not isinstance(fixture, dict):
            return None
        
        venue = fixture.get("venue")
        return venue if isinstance(venue, dict) else None

    async def get_team_venue(self, team_id: int) -> dict | None:
        """
        Get venue data from team endpoint.
        
        Returns team.venue object if exists, else None.
        """
        data = await self.client.get("/teams", params={"id": team_id})
        
        if not isinstance(data, dict) or "response" not in data:
            return None
        
        teams = data.get("response", [])
        if not isinstance(teams, list) or not teams:
            return None
        
        team = teams[0]
        if not isinstance(team, dict):
            return None
        
        venue = team.get("venue")
        return venue if isinstance(venue, dict) else None

    async def get_stadium_data(self, team_id: int) -> dict | None:
        """
        Get team stadium data.
        
        Returns team object with venue/stadium data.
        """
        data = await self.client.get("/teams", params={"id": team_id})
        
        if not isinstance(data, dict) or "response" not in data:
            return None
        
        teams = data.get("response", [])
        if not isinstance(teams, list) or not teams:
            return None
        
        return teams[0] if isinstance(teams[0], dict) else None
