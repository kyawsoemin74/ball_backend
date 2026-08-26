"""create frozen Analytics V1 fact tables"""

from alembic import op
import sqlalchemy as sa


revision = "20260824_analytics_v1"
down_revision = "20260824_team_coach"
branch_labels = None
depends_on = None


def _timestamps():
    return [
        sa.Column("observed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    ]


def upgrade():
    op.create_table(
        "analytics_match_team_statistics",
        sa.Column("statistic_fact_id", sa.BigInteger(), sa.Identity(), primary_key=True, nullable=False),
        sa.Column("match_id", sa.Integer(), nullable=False),
        sa.Column("team_id", sa.Integer(), nullable=False),
        sa.Column("provider_team_id", sa.Text(), nullable=False),
        sa.Column("statistic_name", sa.Text(), nullable=False),
        sa.Column("statistic_label", sa.Text()),
        sa.Column("value_type", sa.Text(), nullable=False),
        sa.Column("value_integer", sa.BigInteger()),
        sa.Column("value_numeric", sa.Numeric(20, 6)),
        sa.Column("value_text", sa.Text()),
        sa.Column("value_boolean", sa.Boolean()),
        sa.Column("source_provider", sa.Text(), server_default="api-football", nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["match_id"], ["matches.fixture_id"], name="fk_analytics_statistics_match"),
        sa.ForeignKeyConstraint(["team_id"], ["teams.team_id"], name="fk_analytics_statistics_team"),
        sa.UniqueConstraint("match_id", "team_id", "statistic_name", name="uq_analytics_statistics_business"),
        sa.CheckConstraint("value_type IN ('integer', 'decimal', 'percent', 'text', 'boolean')", name="ck_analytics_statistics_value_type"),
        sa.CheckConstraint(
            "((value_type = 'integer' AND value_integer IS NOT NULL AND value_numeric IS NULL AND value_text IS NULL AND value_boolean IS NULL) OR "
            "(value_type IN ('decimal', 'percent') AND value_integer IS NULL AND value_numeric IS NOT NULL AND value_text IS NULL AND value_boolean IS NULL) OR "
            "(value_type = 'text' AND value_integer IS NULL AND value_numeric IS NULL AND value_text IS NOT NULL AND value_boolean IS NULL) OR "
            "(value_type = 'boolean' AND value_integer IS NULL AND value_numeric IS NULL AND value_text IS NULL AND value_boolean IS NOT NULL))",
            name="ck_analytics_statistics_typed_value",
        ),
    )
    op.create_index("ix_analytics_statistics_match", "analytics_match_team_statistics", ["match_id"])
    op.create_index("ix_analytics_statistics_team", "analytics_match_team_statistics", ["team_id"])

    op.create_table(
        "analytics_team_season_standings",
        sa.Column("standing_fact_id", sa.BigInteger(), sa.Identity(), primary_key=True, nullable=False),
        sa.Column("league_id", sa.Integer(), nullable=False),
        sa.Column("league_season_id", sa.Integer(), nullable=False),
        sa.Column("team_id", sa.Integer(), nullable=False),
        sa.Column("provider_team_id", sa.Text(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False), sa.Column("points", sa.Integer(), nullable=False),
        sa.Column("played", sa.Integer(), nullable=False), sa.Column("won", sa.Integer(), nullable=False),
        sa.Column("drawn", sa.Integer(), nullable=False), sa.Column("lost", sa.Integer(), nullable=False),
        sa.Column("goals_for", sa.Integer(), nullable=False), sa.Column("goals_against", sa.Integer(), nullable=False),
        sa.Column("goal_difference", sa.Integer(), nullable=False), sa.Column("group_name", sa.Text()),
        sa.Column("form", sa.Text()), sa.Column("description", sa.Text()),
        sa.Column("source_provider", sa.Text(), server_default="api-football", nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["league_id"], ["leagues.league_id"], name="fk_analytics_standings_league"),
        sa.ForeignKeyConstraint(["league_season_id"], ["league_seasons.id"], name="fk_analytics_standings_season"),
        sa.ForeignKeyConstraint(["team_id"], ["teams.team_id"], name="fk_analytics_standings_team"),
        sa.UniqueConstraint("league_season_id", "team_id", name="uq_analytics_standings_business"),
    )
    op.create_index("ix_analytics_standings_league", "analytics_team_season_standings", ["league_id"])
    op.create_index("ix_analytics_standings_season", "analytics_team_season_standings", ["league_season_id"])
    op.create_index("ix_analytics_standings_team", "analytics_team_season_standings", ["team_id"])

    op.create_table(
        "analytics_match_odds_snapshots",
        sa.Column("odds_fact_id", sa.BigInteger(), sa.Identity(), primary_key=True, nullable=False),
        sa.Column("match_id", sa.Integer(), nullable=False), sa.Column("bookmaker_name", sa.Text(), nullable=False),
        sa.Column("market_name", sa.Text(), nullable=False), sa.Column("selection", sa.Text(), nullable=False),
        sa.Column("odd_value", sa.Numeric(12, 6), nullable=False), sa.Column("myanmar_odd", sa.Text()),
        sa.Column("source_provider", sa.Text(), server_default="api-football", nullable=False), *_timestamps(),
        sa.ForeignKeyConstraint(["match_id"], ["matches.fixture_id"], name="fk_analytics_odds_match"),
        sa.UniqueConstraint("match_id", "bookmaker_name", "market_name", "selection", name="uq_analytics_odds_business"),
    )
    op.create_index("ix_analytics_odds_match", "analytics_match_odds_snapshots", ["match_id"])
    op.create_index("ix_analytics_odds_market", "analytics_match_odds_snapshots", ["bookmaker_name", "market_name"])

    op.create_table(
        "analytics_h2h_snapshots",
        sa.Column("h2h_fact_id", sa.BigInteger(), sa.Identity(), primary_key=True, nullable=False),
        sa.Column("team_low_id", sa.Integer(), nullable=False), sa.Column("team_high_id", sa.Integer(), nullable=False),
        sa.Column("source_provider", sa.Text(), server_default="api-football", nullable=False),
        sa.Column("source_fixture_id", sa.BigInteger(), nullable=False), sa.Column("fixture_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fixture_status", sa.Text(), nullable=False), sa.Column("home_provider_team_id", sa.BigInteger(), nullable=False),
        sa.Column("away_provider_team_id", sa.BigInteger(), nullable=False),
        sa.Column("home_goals", sa.Integer()), sa.Column("away_goals", sa.Integer()),
        sa.Column("halftime_home_score", sa.Integer()), sa.Column("halftime_away_score", sa.Integer()),
        sa.Column("fulltime_home_score", sa.Integer()), sa.Column("fulltime_away_score", sa.Integer()),
        sa.Column("extratime_home_score", sa.Integer()), sa.Column("extratime_away_score", sa.Integer()),
        sa.Column("penalty_home_score", sa.Integer()), sa.Column("penalty_away_score", sa.Integer()), *_timestamps(),
        sa.ForeignKeyConstraint(["team_low_id"], ["teams.team_id"], name="fk_analytics_h2h_team_low"),
        sa.ForeignKeyConstraint(["team_high_id"], ["teams.team_id"], name="fk_analytics_h2h_team_high"),
        sa.UniqueConstraint("source_provider", "team_low_id", "team_high_id", "source_fixture_id", name="uq_analytics_h2h_business"),
        sa.CheckConstraint("team_low_id < team_high_id", name="ck_analytics_h2h_team_order"),
    )
    op.create_index("ix_analytics_h2h_pair", "analytics_h2h_snapshots", ["team_low_id", "team_high_id"])
    op.create_index("ix_analytics_h2h_source_fixture", "analytics_h2h_snapshots", ["source_provider", "source_fixture_id"])

    op.create_table(
        "analytics_match_lineups",
        sa.Column("lineup_fact_id", sa.BigInteger(), sa.Identity(), primary_key=True, nullable=False),
        sa.Column("match_id", sa.Integer(), nullable=False), sa.Column("team_id", sa.Integer(), nullable=False),
        sa.Column("player_id", sa.Integer(), nullable=False), sa.Column("provider_team_id", sa.Text(), nullable=False),
        sa.Column("provider_player_id", sa.Text(), nullable=False), sa.Column("roster_role", sa.Text(), nullable=False),
        sa.Column("shirt_number", sa.Integer()), sa.Column("position", sa.Text()), sa.Column("grid", sa.Text()), sa.Column("formation", sa.Text()),
        sa.Column("source_provider", sa.Text(), server_default="api-football", nullable=False), *_timestamps(),
        sa.ForeignKeyConstraint(["match_id"], ["matches.fixture_id"], name="fk_analytics_lineups_match"),
        sa.ForeignKeyConstraint(["team_id"], ["teams.team_id"], name="fk_analytics_lineups_team"),
        sa.ForeignKeyConstraint(["player_id"], ["players.player_id"], name="fk_analytics_lineups_player"),
        sa.UniqueConstraint("match_id", "team_id", "player_id", "roster_role", name="uq_analytics_lineups_business"),
        sa.CheckConstraint("roster_role IN ('STARTER', 'SUBSTITUTE')", name="ck_analytics_lineups_roster_role"),
    )
    op.create_index("ix_analytics_lineups_match", "analytics_match_lineups", ["match_id"])
    op.create_index("ix_analytics_lineups_team", "analytics_match_lineups", ["team_id"])
    op.create_index("ix_analytics_lineups_player", "analytics_match_lineups", ["player_id"])


def downgrade():
    op.drop_table("analytics_match_lineups")
    op.drop_table("analytics_h2h_snapshots")
    op.drop_table("analytics_match_odds_snapshots")
    op.drop_table("analytics_team_season_standings")
    op.drop_table("analytics_match_team_statistics")