import asyncio

from app.models.league import League
from app.services.home_service import HomeService


class FakeLeagueRepository:
    def __init__(self, live_today=None, featured=None, all_leagues=None):
        self._live_today = list(live_today or [])
        self._featured = list(featured or [])
        self._all_leagues = list(all_leagues or [])

    async def get_leagues_with_matches_today(self, db):
        return list(self._live_today)

    async def get_featured_leagues(self, db):
        return list(self._featured)

    async def get_all_leagues(self, db):
        return list(self._all_leagues)


def make_league(league_id, name, country, is_featured=False, display_order=999):
    return League(
        league_id=league_id,
        name=name,
        country=country,
        logo=None,
        season="2024",
        is_featured=is_featured,
        display_order=display_order,
    )


def test_home_service_featured_and_live_today_are_ordered():
    live_today = [
        make_league(9, "Live League", "France", False, 9),
        make_league(7, "Early League", "France", False, 1),
    ]
    featured = [
        make_league(4, "Beta League", "Spain", True, 9),
        make_league(2, "Alpha League", "England", True, 1),
    ]
    all_leagues = live_today + featured + [
        make_league(5, "Non Featured", "Germany", False, 3),
    ]

    service = HomeService(FakeLeagueRepository(live_today=live_today, featured=featured, all_leagues=all_leagues))

    payload = asyncio.run(service.get_home_payload(None))

    assert [item["league_id"] for item in payload["live_today"]] == [7, 9]
    assert [item["league_id"] for item in payload["featured"]] == [2, 4]


def test_home_service_excludes_display_order_above_200_and_keeps_featured_leagues():
    all_leagues = [
        make_league(10, "Hidden League", "England", False, 201),
        make_league(11, "Visible League", "England", False, 150),
    ]

    service = HomeService(
        FakeLeagueRepository(
            featured=[make_league(12, "Featured League", "England", True, 500)],
            all_leagues=all_leagues,
        )
    )

    payload = asyncio.run(service.get_home_payload(None))

    visible_ids = [item["league_id"] for item in payload["countries"][0]["leagues"]]
    featured_ids = [item["league_id"] for item in payload["featured"]]

    assert 11 in visible_ids
    assert 12 in featured_ids
    assert 10 not in visible_ids


def test_home_service_groups_non_featured_countries():
    all_leagues = [
        make_league(1, "Premier League", "England", True, 1),
        make_league(2, "League One", "England", False, 2),
        make_league(3, "Bundesliga", "Germany", False, 1),
    ]

    service = HomeService(FakeLeagueRepository(all_leagues=all_leagues))

    payload = asyncio.run(service.get_home_payload(None))

    assert payload["countries"] == [
        {"type": "country", "country": "England", "leagues": [
            {"league_id": 2, "name": "League One", "country": "England", "country_code": None, "logo": None, "season": "2024", "is_featured": False, "display_order": 2},
        ]},
        {"type": "country", "country": "Germany", "leagues": [
            {"league_id": 3, "name": "Bundesliga", "country": "Germany", "country_code": None, "logo": None, "season": "2024", "is_featured": False, "display_order": 1},
        ]},
    ]
