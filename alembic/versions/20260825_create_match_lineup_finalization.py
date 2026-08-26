"""create durable final lineup finalization state

Revision ID: 20260825_lineup_finalization
Revises: 20260824_analytics_v1
"""

from alembic import op
import sqlalchemy as sa


revision = "20260825_lineup_finalization"
down_revision = "20260824_analytics_v1"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "match_lineup_finalization",
        sa.Column("match_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("required_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_attempted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_category", sa.String(length=40), nullable=True),
        sa.Column("failure_reason", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["match_id"],
            ["matches.fixture_id"],
            name="fk_match_lineup_finalization_match",
            ondelete="NO ACTION",
        ),
        sa.PrimaryKeyConstraint("match_id", name="pk_match_lineup_finalization"),
        sa.CheckConstraint(
            "status IN ('REQUIRED', 'RETRYABLE', 'SUCCESS')",
            name="ck_match_lineup_finalization_status",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name="ck_match_lineup_finalization_attempt_count",
        ),
        sa.CheckConstraint(
            "failure_category IS NULL OR failure_category IN "
            "('PROVIDER_FAILURE', 'INVALID_RESPONSE', 'MASTER_RESOLUTION_FAILURE', "
            "'ANALYTICS_FAILURE', 'DB_FAILURE', 'FLUSH_FAILURE', 'COMMIT_FAILURE', "
            "'LOCK_CONFLICT')",
            name="ck_match_lineup_finalization_failure_category",
        ),
        sa.CheckConstraint(
            "status <> 'RETRYABLE' OR failure_category IS NOT NULL",
            name="ck_match_lineup_finalization_retryable_failure",
        ),
        sa.CheckConstraint(
            "(status = 'SUCCESS' AND completed_at IS NOT NULL AND failure_category IS NULL "
            "AND failure_reason IS NULL) OR "
            "(status IN ('REQUIRED', 'RETRYABLE') AND completed_at IS NULL)",
            name="ck_match_lineup_finalization_state",
        ),
    )
    op.create_index(
        "ix_match_lineup_finalization_retry",
        "match_lineup_finalization",
        ["required_at", "match_id"],
        unique=False,
        postgresql_where=sa.text("status IN ('REQUIRED', 'RETRYABLE')"),
    )


def downgrade():
    op.drop_index("ix_match_lineup_finalization_retry", table_name="match_lineup_finalization")
    op.drop_table("match_lineup_finalization")