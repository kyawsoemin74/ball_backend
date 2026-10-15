"""create durable Odds retry and provider cooldown state

Revision ID: 20261009_odds_retry_state
Revises: 20261006_auth_sessions
"""

from alembic import op
import sqlalchemy as sa


revision = "20261009_odds_retry_state"
down_revision = "20261006_auth_sessions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "odds_refresh_state",
        sa.Column("match_id", sa.Integer(), nullable=False),
        sa.Column("failure_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("next_eligible_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_failure_classification", sa.String(length=64), nullable=True),
        sa.Column("last_attempted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cache_invalidation_pending", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("attempt_id", sa.String(length=36), nullable=True),
        sa.Column("provider_fixture_id", sa.Integer(), nullable=True),
        sa.Column("reconciliation_state", sa.String(length=32), server_default="IDLE", nullable=False),
        sa.Column("pre_attempt_odds_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("pre_attempt_analytics_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("candidate_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("last_reconciliation_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_reconciliation_error", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("failure_count >= 0", name="ck_odds_refresh_state_failure_count"),
        sa.CheckConstraint(
            "reconciliation_state IN ('IDLE', 'ATTEMPT_IN_PROGRESS', 'COMMIT_PENDING', "
            "'COMMIT_CONFIRMED_CACHE_PENDING', 'COMMIT_NOT_APPLIED', 'MANUAL_REVIEW', 'RESOLVED')",
            name="ck_odds_refresh_state_reconciliation_state",
        ),
        sa.ForeignKeyConstraint(
            ["match_id"],
            ["matches.local_match_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("match_id"),
    )
    op.create_index(
        "ix_odds_refresh_state_next_eligible",
        "odds_refresh_state",
        ["next_eligible_at"],
        unique=False,
    )
    op.create_index(
        "ix_odds_refresh_state_cache_pending",
        "odds_refresh_state",
        ["cache_invalidation_pending"],
        unique=False,
    )
    op.create_index(
        "ix_odds_refresh_state_reconciliation",
        "odds_refresh_state",
        ["reconciliation_state"],
        unique=False,
    )
    op.create_table(
        "odds_provider_cooldown",
        sa.Column("provider_key", sa.String(length=100), nullable=False),
        sa.Column("cooldown_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_http_status", sa.Integer(), nullable=True),
        sa.Column("last_failure_classification", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("provider_key"),
    )


def downgrade() -> None:
    op.drop_table("odds_provider_cooldown")
    op.drop_index("ix_odds_refresh_state_reconciliation", table_name="odds_refresh_state")
    op.drop_index("ix_odds_refresh_state_cache_pending", table_name="odds_refresh_state")
    op.drop_index("ix_odds_refresh_state_next_eligible", table_name="odds_refresh_state")
    op.drop_table("odds_refresh_state")
