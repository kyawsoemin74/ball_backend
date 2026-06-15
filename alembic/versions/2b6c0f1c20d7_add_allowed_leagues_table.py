"""add allowed_leagues table

Revision ID: 2b6c0f1c20d7
Revises: c3d4e5f6a7b8
Create Date: 2026-06-15 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "2b6c0f1c20d7"
down_revision = "c3d4e5f6a7b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "allowed_leagues",
        sa.Column("league_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.PrimaryKeyConstraint("league_id", name=op.f("pk_allowed_leagues")),
    )
    op.create_index(op.f("ix_allowed_leagues_league_id"), "allowed_leagues", ["league_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_allowed_leagues_league_id"), table_name="allowed_leagues")
    op.drop_table("allowed_leagues")
