from typing import Optional

from app.providers.base.football_api_client import FootballAPIClient


class H2HProvider:
    """Transport-only provider for H2H-related API-Football calls."""

    def __init__(self, client: FootballAPIClient) -> None:
        self.client = client

    async def get_match_h2h(self, provider_fixture_id: int) -> Optional[dict]:
        if isinstance(provider_fixture_id, bool) or not isinstance(provider_fixture_id, int) or provider_fixture_id <= 0:
            raise ValueError("provider fixture ID must be positive")
        return await self.client.get("/fixtures/headtohead", params={"fixture": provider_fixture_id})

    async def get_h2h_by_team_ids(
        self,
        home_provider_team_id: int,
        away_provider_team_id: int,
    ) -> Optional[dict]:
        if (
            isinstance(home_provider_team_id, bool)
            or isinstance(away_provider_team_id, bool)
            or not isinstance(home_provider_team_id, int)
            or not isinstance(away_provider_team_id, int)
            or home_provider_team_id <= 0
            or away_provider_team_id <= 0
        ):
            raise ValueError("provider team IDs must be positive integers")
        if home_provider_team_id == away_provider_team_id:
            raise ValueError("H2H requires two distinct provider teams")

        h2h_key = f"{home_provider_team_id}-{away_provider_team_id}"
        return await self.client.get("/fixtures/headtohead", params={"h2h": h2h_key})
