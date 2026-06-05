from datetime import datetime, timezone

from app.models.league import League
from app.models.match import Match
from app.repositories.match_repository import MatchRepository


def make_league(league_id, name, is_featured=False, display_order=999):
    return League(
        league_id=league_id,
        name=name,
        country="Test",
        logo=None,
        season="2024",
        is_featured=is_featured,
        display_order=display_order,
    )


def make_match(match_id, league_obj, match_time):
    match = Match(
        match_id=match_id,
        league_id=league_obj.league_id,
        league_name=league_obj.name,
        league_logo=None,
        country_name=league_obj.country,
        country_logo=None,
        match_time=match_time,
        status="NS",
        elapsed=0,
        home_team="Home",
        away_team="Away",
        home_score=0,
        away_score=0,
        venue_name=None,
        venue_city=None,
    )
    match.league_obj = league_obj
    return match


def test_order_matches_for_date_puts_featured_first():
    featured = make_league(1, "Featured League", True, 3)
    non_featured = make_league(2, "Other League", False, 1)

    ordered = MatchRepository.order_matches_for_date([
        make_match(20, non_featured, datetime(2026, 6, 5, 18, 0, tzinfo=timezone.utc)),
        make_match(10, featured, datetime(2026, 6, 5, 17, 0, tzinfo=timezone.utc)),
    ])

    assert [match.match_id for match in ordered] == [10, 20]


def test_order_matches_for_date_uses_display_order_asc():
    first = make_league(1, "Zeta", True, 1)
    second = make_league(2, "Alpha", True, 2)

    ordered = MatchRepository.order_matches_for_date([
        make_match(2, second, datetime(2026, 6, 5, 18, 0, tzinfo=timezone.utc)),
        make_match(1, first, datetime(2026, 6, 5, 17, 0, tzinfo=timezone.utc)),
    ])

    assert [match.match_id for match in ordered] == [1, 2]


def test_order_matches_for_date_sorts_non_featured_alphabetically():
    zeta = make_league(1, "Zeta", False, 9)
    alpha = make_league(2, "Alpha", False, 1)

    ordered = MatchRepository.order_matches_for_date([
        make_match(2, zeta, datetime(2026, 6, 5, 18, 0, tzinfo=timezone.utc)),
        make_match(1, alpha, datetime(2026, 6, 5, 17, 0, tzinfo=timezone.utc)),
    ])

    assert [match.match_id for match in ordered] == [1, 2]
