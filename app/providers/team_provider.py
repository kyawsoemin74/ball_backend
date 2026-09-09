from typing import Optional

from app.providers.base.football_api_client import FootballAPIClient


class TeamProvider:
    """Transport-only provider for team-related API-Football calls."""

    def __init__(self, client: FootballAPIClient) -> None:
        self.client = client

    async def get_team_details(self, team_id: int) -> Optional[dict]:
        return await self.client.get("/teams", params={"id": team_id})

    async def get_league_teams(self, league_id: int, season: int) -> Optional[list[dict]]:
        first_page = await self.client.get(
            "/teams",
            params={"league": league_id, "season": season},
        )
        if not first_page or "response" not in first_page:
            return None

        teams = list(first_page.get("response") or [])
        paging = first_page.get("paging")
        if not isinstance(paging, dict):
            return teams

        current_page = paging.get("current")
        total_pages = paging.get("total")
        if not isinstance(current_page, int) or not isinstance(total_pages, int):
            return teams

        for page in range(current_page + 1, total_pages + 1):
            page_result = await self.client.get(
                "/teams",
                params={"league": league_id, "season": season, "page": page},
            )
            if not page_result or "response" not in page_result:
                return None
            page_teams = page_result.get("response") or []
            if not isinstance(page_teams, list):
                return None
            teams.extend(page_teams)

        return teams

    async def get_team_squad(self, team_id: int) -> Optional[dict]:
        return await self.client.get("/players/squads", params={"team": team_id})

    async def get_team_statistics(self, team_id: int, league_id: int, season: int) -> Optional[dict]:
        return await self.client.get(
            "/teams/statistics",
            params={"team": team_id, "league": league_id, "season": season},
        )
