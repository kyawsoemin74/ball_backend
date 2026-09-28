"""create durable League identity recovery state

Revision ID: 20260922_league_identity_recovery
Revises: 20260915_missing_lineup_identity
"""

from alembic import op
import sqlalchemy as sa


revision = "20260922_league_identity_recovery"
down_revision = "20260915_missing_lineup_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "league_identity_recovery",
        sa.Column("recovery_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("league_id", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=40), nullable=False, server_default="IDENTITY_RESOLUTION_REQUIRED"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_attempted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=100), nullable=True),
        sa.Column("last_error_message", sa.Text(), nullable=True),
        sa.Column("provider", sa.String(length=50), nullable=True),
        sa.Column("resolved_provider_id", sa.String(length=100), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["league_id"], ["leagues.league_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("recovery_id"),
        sa.UniqueConstraint("league_id", name="uq_league_identity_recovery_league_id"),
    )
    op.create_index(
        "ix_league_identity_recovery_league_id",
        "league_identity_recovery",
        ["league_id"],
        unique=False,
    )
    op.create_index(
        "ix_league_identity_recovery_retry",
        "league_identity_recovery",
        ["state", "next_retry_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_league_identity_recovery_retry", table_name="league_identity_recovery")
    op.drop_index("ix_league_identity_recovery_league_id", table_name="league_identity_recovery")
    op.drop_table("league_identity_recovery")