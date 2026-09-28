"""add missing lineup identity tracking

Revision ID: 20260915_missing_lineup_identity
Revises: 20260911_repair_odds_match_fk
"""

from alembic import op
import sqlalchemy as sa

revision = "20260915_missing_lineup_identity"
down_revision = "20260911_repair_odds_match_fk"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "missing_lineup_identities",
        sa.Column("missing_identity_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("match_id", sa.Integer(), nullable=False),
        sa.Column("provider_fixture_id", sa.String(length=100), nullable=True),
        sa.Column("local_team_id", sa.Integer(), nullable=True),
        sa.Column("provider_team_id", sa.String(length=100), nullable=False),
        sa.Column("provider", sa.String(length=50), server_default="api-football", nullable=False),
        sa.Column("player_name", sa.String(length=255), nullable=True),
        sa.Column("shirt_number", sa.Integer(), nullable=True),
        sa.Column("position", sa.String(length=50), nullable=True),
        sa.Column("grid", sa.String(length=50), nullable=True),
        sa.Column("roster_role", sa.String(length=20), nullable=False),
        sa.Column("lineup_position", sa.Integer(), nullable=False),
        sa.Column("missing_reason", sa.String(length=100), nullable=False),
        sa.Column("resolution_status", sa.String(length=30), server_default="MISSING", nullable=False),
        sa.Column("retry_eligible", sa.Integer(), server_default="1", nullable=False),
        sa.Column("observation_count", sa.Integer(), server_default="1", nullable=False),
        sa.Column("first_seen", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("resolved_local_player_id", sa.Integer(), nullable=True),
        sa.Column("raw_identity_state", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["match_id"], ["matches.local_match_id"]),
        sa.ForeignKeyConstraint(["local_team_id"], ["teams.team_id"]),
        sa.ForeignKeyConstraint(["resolved_local_player_id"], ["players.player_id"]),
        sa.PrimaryKeyConstraint("missing_identity_id"),
        sa.UniqueConstraint("match_id", "provider_team_id", "roster_role", "lineup_position", name="uq_missing_lineup_identity_entry"),
    )
    op.create_index("ix_missing_lineup_identities_match_id", "missing_lineup_identities", ["match_id"])
    op.drop_constraint("ck_match_lineup_finalization_failure_category", "match_lineup_finalization", type_="check")
    op.create_check_constraint(
        "ck_match_lineup_finalization_failure_category",
        "match_lineup_finalization",
        "failure_category IS NULL OR failure_category IN ('PROVIDER_FAILURE', 'INVALID_RESPONSE', 'LINEUP_PARTIAL', 'MASTER_RESOLUTION_FAILURE', 'ANALYTICS_FAILURE', 'DB_FAILURE', 'FLUSH_FAILURE', 'COMMIT_FAILURE', 'LOCK_CONFLICT', 'SYNC_UNAVAILABLE', 'IDENTITY_BOUNDARY_VIOLATION', 'MAX_RETRY_ATTEMPTS_EXCEEDED')",
    )


def downgrade():
    op.drop_constraint("ck_match_lineup_finalization_failure_category", "match_lineup_finalization", type_="check")
    op.create_check_constraint(
        "ck_match_lineup_finalization_failure_category",
        "match_lineup_finalization",
        "failure_category IS NULL OR failure_category IN ('PROVIDER_FAILURE', 'INVALID_RESPONSE', 'MASTER_RESOLUTION_FAILURE', 'ANALYTICS_FAILURE', 'DB_FAILURE', 'FLUSH_FAILURE', 'COMMIT_FAILURE', 'LOCK_CONFLICT', 'SYNC_UNAVAILABLE', 'IDENTITY_BOUNDARY_VIOLATION', 'MAX_RETRY_ATTEMPTS_EXCEEDED')",
    )
    op.drop_index("ix_missing_lineup_identities_match_id", table_name="missing_lineup_identities")
    op.drop_table("missing_lineup_identities")
