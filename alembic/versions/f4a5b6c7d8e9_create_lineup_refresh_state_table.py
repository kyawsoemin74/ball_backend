"""create lineup refresh state table

Revision ID: f4a5b6c7d8e9
Revises: e4f5a6b7c8d9
Create Date: 2026-06-23 00:00:00.000000+00:00
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "f4a5b6c7d8e9"
down_revision = "e4f5a6b7c8d9"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "lineup_refresh_state",
        sa.Column("match_id", sa.Integer(), sa.ForeignKey("matches.fixture_id"), primary_key=True, nullable=False),
        sa.Column("last_refreshed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_lineup_refresh_state_match_id", "lineup_refresh_state", ["match_id"], unique=False)


def downgrade():
    op.drop_index("ix_lineup_refresh_state_match_id", table_name="lineup_refresh_state")
    op.drop_table("lineup_refresh_state")
