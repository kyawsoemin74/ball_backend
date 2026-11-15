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
        first_page = await self.client.get("/fixtures", params={"date": target_date})
        if not isinstance(first_page, dict) or first_page.get("errors"):
            return None

        paging = first_page.get("paging")
        if not isinstance(paging, dict):
            return None

        current_page = paging.get("current")
        total_pages = paging.get("total")
        if (
            type(current_page) is not int
            or type(total_pages) is not int
            or current_page != 1
            or total_pages < 1
        ):
            return None

        combined_fixtures = []
        seen_fixture_ids = set()
        for page in range(1, total_pages + 1):
            if page == 1:
                page_result = first_page
            else:
                page_result = await self.client.get(
                    "/fixtures",
                    params={"date": target_date, "page": page},
                )

            if not isinstance(page_result, dict) or page_result.get("errors"):
                return None

            page_fixtures = page_result.get("response")
            page_paging = page_result.get("paging")
            if not isinstance(page_fixtures, list) or not isinstance(page_paging, dict):
                return None

            page_current = page_paging.get("current")
            page_total = page_paging.get("total")
            if (
                type(page_current) is not int
                or type(page_total) is not int
                or page_current != page
                or page_total != total_pages
            ):
                return None

            for fixture in page_fixtures:
                if not isinstance(fixture, dict) or not isinstance(fixture.get("fixture"), dict):
                    return None

                provider_fixture_id = fixture["fixture"].get("id")
                if isinstance(provider_fixture_id, bool) or provider_fixture_id is None:
                    return None
                try:
                    provider_fixture_id = int(provider_fixture_id)
                except (TypeError, ValueError):
                    return None
                if provider_fixture_id <= 0:
                    return None

                if provider_fixture_id not in seen_fixture_ids:
                    seen_fixture_ids.add(provider_fixture_id)
                    combined_fixtures.append(fixture)

        merged_result = dict(first_page)
        merged_result["response"] = combined_fixtures
        return merged_result

    async def get_live_fixtures(self) -> Optional[dict]:
        return await self.client.get("/fixtures", params={"live": "all"})

    async def get_fixtures_by_ids(self, fixture_ids: Sequence[str | int]) -> Optional[dict]:
        return await self.client.get("/fixtures", params={"ids": "-".join(str(item) for item in fixture_ids)})
