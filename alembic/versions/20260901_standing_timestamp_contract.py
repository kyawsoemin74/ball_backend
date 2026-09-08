"""enforce standing timestamp contract

Revision ID: 20260901_standing_ts
Revises: 20260829_league_name_unique
Create Date: 2026-09-01 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "20260901_standing_ts"
down_revision = "20260829_league_name_unique"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        UPDATE standings
        SET updated_at = created_at
        WHERE updated_at IS NULL
        """
    )
    op.alter_column(
        "standings",
        "updated_at",
        existing_type=sa.DateTime(timezone=True),
        server_default=sa.func.now(),
        nullable=False,
    )


def downgrade():
    op.alter_column(
        "standings",
        "updated_at",
        existing_type=sa.DateTime(timezone=True),
        server_default=None,
        nullable=True,
    )
