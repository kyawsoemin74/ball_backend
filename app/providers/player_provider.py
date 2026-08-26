from typing import Optional

from app.services.base.football_client import FootballAPIClient


class PlayerProvider:
    """Transport-only provider for player-related API-Football calls."""

    def __init__(self, client: FootballAPIClient) -> None:
        self.client = client

    async def get_player(self, player_id: int) -> Optional[dict]:
        """Get player details by API-Football player ID."""
        return await self.client.get("/players", params={"id": player_id})

    async def get_team_squad(self, team_id: int) -> Optional[dict]:
        """Get team squad - contains player data with rich profile info."""
        return await self.client.get("/players/squads", params={"team": team_id})

    async def get_fixture_lineups(self, fixture_id: int) -> Optional[dict]:
        """Get fixture lineups - contains player data with position/number info."""
        return await self.client.get("/fixtures/lineups", params={"fixture": fixture_id})

    async def get_fixture_events(self, fixture_id: int) -> Optional[dict]:
        """Get fixture events - contains scorer/assist player data."""
        return await self.client.get("/fixtures/events", params={"fixture": fixture_id})

    async def get_league_top_scorers(self, league_id: int, season: int) -> Optional[dict]:
        """Get top scorers for a league/season - contains player data with stats."""
        return await self.client.get("/players/topscorers", params={"league": league_id, "season": season})
