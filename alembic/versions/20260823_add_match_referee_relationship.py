"""add nullable Match to Referee relationship

Revision ID: 20260823_match_referee
Revises: 20260823_match_venue
Create Date: 2026-08-23 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "20260823_match_referee"
down_revision = "20260823_match_venue"
branch_labels = None
depends_on = None


_CONSTRAINT_NAME = "fk_matches_referee_id_referees"
_INDEX_NAME = "ix_matches_referee_id"


def upgrade() -> None:
    op.add_column(
        "matches",
        sa.Column("referee_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        _CONSTRAINT_NAME,
        "matches",
        "referees",
        ["referee_id"],
        ["referee_id"],
        ondelete="RESTRICT",
    )
    op.create_index(_INDEX_NAME, "matches", ["referee_id"], unique=False)


def downgrade() -> None:
    op.drop_index(_INDEX_NAME, table_name="matches")
    op.drop_constraint(_CONSTRAINT_NAME, "matches", type_="foreignkey")
    op.drop_column("matches", "referee_id")
