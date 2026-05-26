"""initial migration

Revision ID: 0001_initial
Revises: 
Create Date: 2026-05-20 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "matches",
        sa.Column("fixture_id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("league_id", sa.Integer(), nullable=False),
        sa.Column("league_name", sa.String(length=255), nullable=True),
        sa.Column("league_logo", sa.String(length=500), nullable=True),
        sa.Column("country_name", sa.String(length=255), nullable=True),
        sa.Column("country_logo", sa.String(length=500), nullable=True),
        sa.Column("match_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("elapsed", sa.Integer(), nullable=True),
        sa.Column("home_team", sa.String(length=255), nullable=False),
        sa.Column("home_team_logo", sa.String(length=500), nullable=True),
        sa.Column("away_team", sa.String(length=255), nullable=False),
        sa.Column("away_team_logo", sa.String(length=500), nullable=True),
        sa.Column("home_score", sa.Integer(), nullable=False),
        sa.Column("away_score", sa.Integer(), nullable=False),
        sa.Column("venue_name", sa.String(length=255), nullable=True),
        sa.Column("venue_city", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(op.f("ix_matches_league_id"), "matches", ["league_id"], unique=False)

    op.create_table(
        "leagues",
        sa.Column("league_id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("country", sa.String(length=255), nullable=True),
        sa.Column("logo", sa.String(length=500), nullable=True),
        sa.Column("season", sa.String(length=10), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("name", name=op.f("uq_leagues_name")),
    )

    op.create_table(
        "teams",
        sa.Column("team_id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("country", sa.String(length=255), nullable=True),
        sa.Column("logo", sa.String(length=500), nullable=True),
        sa.Column("stadium", sa.String(length=255), nullable=True),
        sa.Column("founded", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("name", name=op.f("uq_teams_name")),
    )

    op.create_table(
        "standings",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("league_id", sa.Integer(), nullable=False),
        sa.Column("season", sa.String(length=10), nullable=False),
        sa.Column("team_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("points", sa.Integer(), nullable=False),
        sa.Column("played", sa.Integer(), nullable=False),
        sa.Column("won", sa.Integer(), nullable=False),
        sa.Column("drawn", sa.Integer(), nullable=False),
        sa.Column("lost", sa.Integer(), nullable=False),
        sa.Column("goals_for", sa.Integer(), nullable=False),
        sa.Column("goals_against", sa.Integer(), nullable=False),
        sa.Column("goal_difference", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["league_id"], ["leagues.league_id"], name=op.f("fk_standings_league_id_leagues")),
        sa.ForeignKeyConstraint(["team_id"], ["teams.team_id"], name=op.f("fk_standings_team_id_teams")),
    )

    op.create_table(
        "ads",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("image_url", sa.String(length=500), nullable=True),
        sa.Column("link_url", sa.String(length=500), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "news",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("category", sa.String(length=50), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "odds",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("fixture_id", sa.Integer(), nullable=False),
        sa.Column("bookmaker_name", sa.String(length=255), nullable=True),
        sa.Column("market_name", sa.String(length=255), nullable=False),
        sa.Column("selection", sa.String(length=255), nullable=False),
        sa.Column("odd_value", sa.String(length=50), nullable=False),
        sa.Column("last_updated", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["fixture_id"], ["matches.fixture_id"], name=op.f("fk_odds_fixture_id_matches")),
        sa.UniqueConstraint("fixture_id", "bookmaker_name", "market_name", "selection", name=op.f("uq_odds_fixture_bookmaker_market_selection")),
    )
    op.create_index(op.f("ix_odds_fixture_id"), "odds", ["fixture_id"], unique=False)
    op.create_index(op.f("ix_odds_market_name"), "odds", ["market_name"], unique=False)
    op.create_index(op.f("ix_odds_selection"), "odds", ["selection"], unique=False)


def downgrade():
    op.drop_index(op.f("ix_odds_selection"), table_name="odds")
    op.drop_index(op.f("ix_odds_market_name"), table_name="odds")
    op.drop_index(op.f("ix_odds_fixture_id"), table_name="odds")
    op.drop_table("odds")
    op.drop_table("news")
    op.drop_table("ads")
    op.drop_table("standings")
    op.drop_table("teams")
    op.drop_table("leagues")
    op.drop_index(op.f("ix_matches_league_id"), table_name="matches")
    op.drop_table("matches")
