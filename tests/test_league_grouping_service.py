from app.models.league import League
from app.services.league_grouping_service import LeagueGroupingService


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


def test_grouping_service_puts_featured_first_and_groups_countries():
    service = LeagueGroupingService()

    leagues = [
        make_league(4, "Bundesliga 2", "Germany", False, 5),
        make_league(1, "Premier League", "England", True, 1),
        make_league(2, "Champions League", "Europe", True, 2),
        make_league(3, "League One", "England", False, 3),
        make_league(5, "La Liga", "Spain", True, 4),
        make_league(6, "Segunda Division", "Spain", False, 6),
    ]

    groups = service.build_groups(leagues)

    assert [group["type"] for group in groups] == ["featured", "country", "country", "country"]
    assert groups[0] == {
        "type": "featured",
        "title": "Featured Leagues",
        "leagues": [
            {"league_id": 1, "name": "Premier League", "country": "England", "logo": None, "season": "2024", "is_featured": True, "display_order": 1},
            {"league_id": 2, "name": "Champions League", "country": "Europe", "logo": None, "season": "2024", "is_featured": True, "display_order": 2},
            {"league_id": 5, "name": "La Liga", "country": "Spain", "logo": None, "season": "2024", "is_featured": True, "display_order": 4},
        ],
    }
    assert groups[1] == {
        "type": "country",
        "country": "England",
        "leagues": [
            {"league_id": 3, "name": "League One", "country": "England", "logo": None, "season": "2024", "is_featured": False, "display_order": 3},
        ],
    }
    assert groups[2] == {
        "type": "country",
        "country": "Germany",
        "leagues": [
            {"league_id": 4, "name": "Bundesliga 2", "country": "Germany", "logo": None, "season": "2024", "is_featured": False, "display_order": 5},
        ],
    }
    assert groups[3] == {
        "type": "country",
        "country": "Spain",
        "leagues": [
            {"league_id": 6, "name": "Segunda Division", "country": "Spain", "logo": None, "season": "2024", "is_featured": False, "display_order": 6},
        ],
    }
