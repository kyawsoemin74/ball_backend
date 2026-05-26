"""create match_lineups table fix
Revision ID: 2c248ce5b79b
Revises: afe19b147c27
Create Date: 2026-05-23 16:18:31.410505+00:00
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '2c248ce5b79b'
down_revision = '418a125d5c15'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "match_lineups",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("match_id", sa.Integer(), sa.ForeignKey("matches.fixture_id"), nullable=False, unique=True, index=True),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now()),
    )


def downgrade():
    op.drop_table("match_lineups")
