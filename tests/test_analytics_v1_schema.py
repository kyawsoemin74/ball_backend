from sqlalchemy import CheckConstraint, UniqueConstraint

from app.db import Base
from app.models.analytics import (
    AnalyticsH2HSnapshot,
    AnalyticsMatchLineup,
    AnalyticsMatchOddsSnapshot,
    AnalyticsMatchTeamStatistic,
    AnalyticsTeamSeasonStanding,
)


FACTS = [
    AnalyticsMatchTeamStatistic,
    AnalyticsTeamSeasonStanding,
    AnalyticsMatchOddsSnapshot,
    AnalyticsH2HSnapshot,
    AnalyticsMatchLineup,
]


def _constraint_names(table, kind):
    return {constraint.name for constraint in table.constraints if isinstance(constraint, kind)}


def test_analytics_v1_contains_exactly_five_fact_tables():
    names = {fact.__tablename__ for fact in FACTS}
    assert names == {
        "analytics_match_team_statistics",
        "analytics_team_season_standings",
        "analytics_match_odds_snapshots",
        "analytics_h2h_snapshots",
        "analytics_match_lineups",
    }
    assert "analytics_match_events" not in Base.metadata.tables


def test_analytics_v1_surrogate_keys_and_business_uniques():
    expected = {
        AnalyticsMatchTeamStatistic: ("statistic_fact_id", "uq_analytics_statistics_business"),
        AnalyticsTeamSeasonStanding: ("standing_fact_id", "uq_analytics_standings_business"),
        AnalyticsMatchOddsSnapshot: ("odds_fact_id", "uq_analytics_odds_business"),
        AnalyticsH2HSnapshot: ("h2h_fact_id", "uq_analytics_h2h_business"),
        AnalyticsMatchLineup: ("lineup_fact_id", "uq_analytics_lineups_business"),
    }
    for model, (pk, unique) in expected.items():
        assert list(model.__table__.primary_key.columns.keys()) == [pk]
        assert unique in _constraint_names(model.__table__, UniqueConstraint)


def test_analytics_v1_checks_are_frozen():
    assert _constraint_names(AnalyticsMatchTeamStatistic.__table__, CheckConstraint) == {
        "ck_analytics_statistics_value_type",
        "ck_analytics_statistics_typed_value",
    }
    assert _constraint_names(AnalyticsH2HSnapshot.__table__, CheckConstraint) == {"ck_analytics_h2h_team_order"}
    assert _constraint_names(AnalyticsMatchLineup.__table__, CheckConstraint) == {"ck_analytics_lineups_roster_role"}


def test_analytics_v1_required_identity_columns_are_non_nullable():
    required = {
        AnalyticsMatchTeamStatistic: {"match_id", "team_id", "provider_team_id", "statistic_name", "value_type"},
        AnalyticsTeamSeasonStanding: {"league_id", "league_season_id", "team_id", "provider_team_id"},
        AnalyticsMatchOddsSnapshot: {"match_id", "bookmaker_name", "market_name", "selection", "odd_value"},
        AnalyticsH2HSnapshot: {"team_low_id", "team_high_id", "source_fixture_id"},
        AnalyticsMatchLineup: {"match_id", "team_id", "player_id", "provider_team_id", "provider_player_id", "roster_role"},
    }
    for model, columns in required.items():
        assert all(not model.__table__.c[column].nullable for column in columns)


def test_analytics_v1_foreign_keys_and_defaults_are_declared():
    expected_fks = {
        AnalyticsMatchTeamStatistic: {"matches.fixture_id", "teams.team_id"},
        AnalyticsTeamSeasonStanding: {"leagues.league_id", "league_seasons.id", "teams.team_id"},
        AnalyticsMatchOddsSnapshot: {"matches.fixture_id"},
        AnalyticsH2HSnapshot: {"teams.team_id"},
        AnalyticsMatchLineup: {"matches.fixture_id", "teams.team_id", "players.player_id"},
    }
    for model, targets in expected_fks.items():
        assert {fk.target_fullname for fk in model.__table__.foreign_keys} == targets
        assert all(fk.ondelete is None and fk.onupdate is None for fk in model.__table__.foreign_keys)
        assert str(model.__table__.c.source_provider.server_default.arg) == "api-football"
        for column in ("observed_at", "created_at", "updated_at"):
            assert model.__table__.c[column].server_default is not None