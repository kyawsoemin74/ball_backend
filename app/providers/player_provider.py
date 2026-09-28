from typing import Optional

from app.providers.base.football_api_client import FootballAPIClient


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

    async def collect_team_squad(self, team_id: int) -> dict:
        """Collect one complete squad without mixing persistence into transport."""
        payload = await self.get_team_squad(team_id)
        if not isinstance(payload, dict) or not isinstance(payload.get("response"), list):
            return {
                "provider": "api-football",
                "team_provider_id": str(team_id),
                "players": [],
                "provider_request_status": "INVALID_RESPONSE",
                "pagination": {"current": 1, "total": 1, "requests": 1},
                "error": "squad response is missing a response list",
            }

        response = payload.get("response") or []
        players = []
        for item in response:
            if isinstance(item, dict) and isinstance(item.get("players"), list):
                players.extend(item["players"])

        paging = payload.get("paging") if isinstance(payload.get("paging"), dict) else None
        current = paging.get("current") if paging else 1
        total = paging.get("total") if paging else 1
        if not isinstance(current, int) or not isinstance(total, int) or total < current:
            current, total = 1, 1

        requests = 1
        for page in range(current + 1, total + 1):
            page_payload = await self.client.get(
                "/players/squads",
                params={"team": team_id, "page": page},
            )
            requests += 1
            if not isinstance(page_payload, dict) or not isinstance(page_payload.get("response"), list):
                return {
                    "provider": "api-football",
                    "team_provider_id": str(team_id),
                    "players": players,
                    "provider_request_status": "PAGINATION_FAILURE",
                    "pagination": {"current": page, "total": total, "requests": requests},
                    "error": "squad pagination response is invalid",
                }
            for item in page_payload["response"]:
                if isinstance(item, dict) and isinstance(item.get("players"), list):
                    players.extend(item["players"])

        return {
            "provider": "api-football",
            "team_provider_id": str(team_id),
            "players": players,
            "provider_request_status": "SUCCESS",
            "pagination": {"current": total, "total": total, "requests": requests},
            "error": None,
        }

    async def get_fixture_lineups(self, fixture_id: int) -> Optional[dict]:
        """Get fixture lineups - contains player data with position/number info."""
        return await self.client.get("/fixtures/lineups", params={"fixture": fixture_id})

    async def get_fixture_events(self, fixture_id: int) -> Optional[dict]:
        """Get fixture events - contains scorer/assist player data."""
        return await self.client.get("/fixtures/events", params={"fixture": fixture_id})

    async def get_league_top_scorers(self, league_id: int, season: int) -> Optional[dict]:
        """Get top scorers for a league/season - contains player data with stats."""
        return await self.client.get("/players/topscorers", params={"league": league_id, "season": season})
