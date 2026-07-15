from typing import Optional, Sequence

from app.providers.base.football_api_client import FootballAPIClient


class FixtureProvider:
    """Transport-only provider for fixture-related API-Football calls."""

    def __init__(self, client: FootballAPIClient) -> None:
        self.client = client

    async def get_fixtures(self, league: int, season: int) -> Optional[dict]:
        first_page = await self.client.get("/fixtures", params={"league": league, "season": season})
        if not first_page or "response" not in first_page:
            return first_page

        paging = first_page.get("paging") if isinstance(first_page, dict) else None
        if not isinstance(paging, dict):
            return first_page

        current_page = paging.get("current")
        total_pages = paging.get("total")
        if not isinstance(current_page, int) or not isinstance(total_pages, int):
            return first_page
        if total_pages <= current_page:
            return first_page

        combined_fixtures = list(first_page.get("response", []))

        for page in range(current_page + 1, total_pages + 1):
            page_result = await self.client.get(
                "/fixtures",
                params={"league": league, "season": season, "page": page},
            )
            if not page_result or "response" not in page_result:
                return None

            page_fixtures = page_result.get("response", [])
            if isinstance(page_fixtures, list):
                combined_fixtures.extend(page_fixtures)

        merged_result = dict(first_page)
        merged_result["response"] = combined_fixtures
        return merged_result

    async def get_fixtures_by_date(self, target_date: str) -> Optional[dict]:
        return await self.client.get("/fixtures", params={"date": target_date})

    async def get_live_fixtures(self) -> Optional[dict]:
        return await self.client.get("/fixtures", params={"live": "all"})

    async def get_fixtures_by_ids(self, fixture_ids: Sequence[str | int]) -> Optional[dict]:
        return await self.client.get("/fixtures", params={"ids": "-".join(str(item) for item in fixture_ids)})
