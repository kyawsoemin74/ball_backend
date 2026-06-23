"""add match season owner

Revision ID: c9d4a8b7e6f1
Revises: a1b2c3d4e5f6
Create Date: 2026-06-22 00:00:00.000000+00:00
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "c9d4a8b7e6f1"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("matches", sa.Column("season", sa.Integer(), nullable=True))
    op.create_index("ix_matches_league_id_season", "matches", ["league_id", "season"], unique=False)


def downgrade():
    op.drop_index("ix_matches_league_id_season", table_name="matches")
    op.drop_column("matches", "season")
