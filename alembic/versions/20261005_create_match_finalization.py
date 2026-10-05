"""create durable Match finalization state

Revision ID: 20261005_match_finalization
Revises: 20260926_standing_ls_identity
"""

from alembic import op
import sqlalchemy as sa


revision = "20261005_match_finalization"
down_revision = "20260926_standing_ls_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "match_finalization",
        sa.Column("match_id", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=20), server_default="PENDING", nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_attempted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_category", sa.String(length=50), nullable=True),
        sa.Column("last_error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "state IN ('PENDING', 'RUNNING', 'SUCCESS', 'FAILED', 'EXHAUSTED')",
            name="ck_match_finalization_state",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name="ck_match_finalization_attempt_count",
        ),
        sa.CheckConstraint(
            "(state = 'SUCCESS' AND completed_at IS NOT NULL) OR "
            "(state != 'SUCCESS' AND completed_at IS NULL)",
            name="ck_match_finalization_completed",
        ),
        sa.ForeignKeyConstraint(
            ["match_id"],
            ["matches.local_match_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("match_id"),
    )
    op.create_index(
        "ix_match_finalization_retry",
        "match_finalization",
        ["state", "next_retry_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_match_finalization_retry", table_name="match_finalization")
    op.drop_table("match_finalization")
