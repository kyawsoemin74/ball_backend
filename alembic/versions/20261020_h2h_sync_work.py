"""Create durable H2H sync work queue.

Revision ID: 20261020_h2h_sync_work
Revises: 20261017_odds_giving_team
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20261020_h2h_sync_work"
down_revision = "20261017_odds_giving_team"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "h2h_sync_work",
        sa.Column(
            "local_match_id",
            sa.Integer(),
            sa.ForeignKey("matches.local_match_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("pair_key", sa.String(length=50), nullable=False),
        sa.Column("match_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="PENDING", nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("retryable", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=100), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claim_token", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_outcome_count", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("local_match_id", name="h2h_sync_work_pkey"),
        sa.CheckConstraint(
            "status IN ('PENDING', 'PROCESSING', 'SUCCESS', 'EMPTY', 'FAILED')",
            name="ck_h2h_sync_work_status",
        ),
        sa.CheckConstraint("attempt_count >= 0", name="ck_h2h_sync_work_attempt_count"),
        sa.CheckConstraint(
            "status != 'PROCESSING' OR "
            "(claimed_at IS NOT NULL AND lease_expires_at IS NOT NULL AND claim_token IS NOT NULL)",
            name="ck_h2h_sync_work_processing_lease",
        ),
        sa.CheckConstraint(
            "status != 'FAILED' OR retryable = FALSE OR next_retry_at IS NOT NULL",
            name="ck_h2h_sync_work_failed_retry",
        ),
    )
    op.create_index(
        "ix_h2h_sync_work_due",
        "h2h_sync_work",
        ["status", "next_retry_at", "match_time", "local_match_id"],
        postgresql_where=sa.text("status IN ('PENDING', 'FAILED')"),
    )
    op.create_index(
        "ix_h2h_sync_work_expired_lease",
        "h2h_sync_work",
        ["lease_expires_at"],
        postgresql_where=sa.text("status = 'PROCESSING'"),
    )
    op.create_index(
        "ix_h2h_sync_work_pair_status",
        "h2h_sync_work",
        ["pair_key", "status"],
    )


def downgrade():
    op.drop_index("ix_h2h_sync_work_pair_status", table_name="h2h_sync_work")
    op.drop_index("ix_h2h_sync_work_expired_lease", table_name="h2h_sync_work")
    op.drop_index("ix_h2h_sync_work_due", table_name="h2h_sync_work")
    op.drop_table("h2h_sync_work")
