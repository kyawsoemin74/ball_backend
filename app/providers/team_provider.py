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

    async def collect_team_evidence(
        self,
        *,
        team_name: str | None = None,
        country: str | None = None,
        league_id: int | None = None,
        season: int | str | None = None,
    ) -> dict:
        """Read-only evidence collector for API-Football team metadata.

        It returns a provider evidence object containing the provider request status and
        any provider team candidates collected without mutating the local Team database.
        """
        if league_id is None or season is None:
            return {
                "provider_request_status": "NO_CANDIDATE",
                "candidates": [],
            }

        try:
            raw = await self.get_league_teams(int(league_id), int(season))
        except Exception:
            return {
                "provider_request_status": "PROVIDER_ERROR",
                "candidates": [],
            }

        if not isinstance(raw, list):
            return {
                "provider_request_status": "NO_CANDIDATE",
                "candidates": [],
            }

        candidates = []
        name_key = (team_name or "").strip().lower()
        country_key = (country or "").strip().lower()
        for item in raw:
            if not isinstance(item, dict):
                continue
            payload = item.get("team") if isinstance(item.get("team"), dict) else item
            if not isinstance(payload, dict):
                continue
            provider_id = payload.get("id")
            provider_name = payload.get("name")
            provider_country = payload.get("country")
            if provider_id is None:
                continue
            if provider_name and name_key and provider_name.strip().lower() != name_key:
                continue
            if provider_country and country_key and provider_country.strip().lower() != country_key:
                continue
            candidates.append({
                "provider_id": provider_id,
                "team_name": provider_name,
                "country": provider_country,
                "league": item.get("league") or {},
                "season": season,
                "logo": payload.get("logo"),
                "stadium": payload.get("venue") or payload.get("stadium"),
                "raw": item,
            })

        if not candidates:
            return {
                "provider_request_status": "SUCCESS",
                "candidates": [],
            }

        return {
            "provider_request_status": "SUCCESS",
            "candidates": candidates,
        }

    async def get_team_squad(self, team_id: int) -> Optional[dict]:
        return await self.client.get("/players/squads", params={"team": team_id})

    async def get_team_statistics(self, team_id: int, league_id: int, season: int) -> Optional[dict]:
        return await self.client.get(
            "/teams/statistics",
            params={"team": team_id, "league": league_id, "season": season},
        )
