"""separate local Match identity from provider fixture identity

Revision ID: 20260910_separate_match_identity
Revises: 20260907_add_match_league_fk
"""

from alembic import op
import sqlalchemy as sa


revision = "20260910_separate_match_identity"
down_revision = "20260907_add_match_league_fk"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("matches")}

    if "provider_fixture_id" not in columns:
        op.alter_column("matches", "fixture_id", new_column_name="provider_fixture_id")
    if "local_match_id" not in columns:
        op.add_column("matches", sa.Column("local_match_id", sa.Integer(), nullable=True))
        op.execute("UPDATE matches SET local_match_id = provider_fixture_id WHERE local_match_id IS NULL")

    op.alter_column("matches", "local_match_id", nullable=False)
    if "provider" not in columns:
        op.add_column("matches", sa.Column("provider", sa.String(length=50), nullable=False, server_default="api-football"))

    child_foreign_keys = []
    for table in inspector.get_table_names():
        if table == "matches":
            continue
        for fk in inspector.get_foreign_keys(table):
            if fk.get("referred_table") == "matches":
                child_foreign_keys.append((table, fk))
                op.drop_constraint(fk["name"], table, type_="foreignkey")

    pk = next((key for key in inspector.get_pk_constraint("matches").get("constrained_columns", []) if key == "provider_fixture_id"), None)
    if pk:
        op.drop_constraint(inspector.get_pk_constraint("matches")["name"], "matches", type_="primary")
        op.create_primary_key("pk_matches_local_match_id", "matches", ["local_match_id"])

    existing_unique = {constraint.get("name") for constraint in inspector.get_unique_constraints("matches")}
    if "uq_matches_provider_fixture_id" not in existing_unique:
        op.create_unique_constraint("uq_matches_provider_fixture_id", "matches", ["provider", "provider_fixture_id"])

    # Existing child values were equal to the old fixture key, so changing their
    # foreign-key target preserves data while making the ownership explicit.
    for table, child_column, constraint in (
        ("match_lineups", "local_match_id", "fk_match_lineups_local_match_id_matches"),
        ("match_events", "match_id", "fk_match_events_match_id_matches"),
        ("match_statistics", "match_id", "fk_match_statistics_match_id_matches"),
        ("analytics_match_lineups", "match_id", "fk_analytics_lineups_match"),
        ("analytics_match_team_statistics", "match_id", "fk_analytics_statistics_match"),
        ("analytics_match_odds_snapshots", "match_id", "fk_analytics_odds_match"),
        ("match_lineup_finalization", "match_id", "fk_match_lineup_finalization_match_id_matches"),
        ("lineup_refresh_state", "match_id", "fk_lineup_refresh_state_match_id_matches"),
    ):
        if table not in inspector.get_table_names():
            continue
        if table == "match_lineups" and "local_match_id" not in {column["name"] for column in inspector.get_columns(table)}:
            op.alter_column(table, "match_id", new_column_name="local_match_id")
        op.create_foreign_key(constraint, table, "matches", [child_column], ["local_match_id"])


def downgrade():
    raise NotImplementedError("Match identity separation cannot be safely downgraded without restoring provider-coupled foreign keys")