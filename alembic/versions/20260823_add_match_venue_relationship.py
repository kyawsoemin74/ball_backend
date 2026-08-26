"""add nullable Match to Venue relationship

Revision ID: 20260823_match_venue
Revises: 20260822_align_league_140
Create Date: 2026-08-23 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "20260823_match_venue"
down_revision = "20260822_align_league_140"
branch_labels = None
depends_on = None


_CONSTRAINT_NAME = "fk_matches_venue_id_venues"
_INDEX_NAME = "ix_matches_venue_id"


def upgrade() -> None:
    op.add_column(
        "matches",
        sa.Column("venue_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        _CONSTRAINT_NAME,
        "matches",
        "venues",
        ["venue_id"],
        ["venue_id"],
        ondelete="RESTRICT",
    )
    op.create_index(_INDEX_NAME, "matches", ["venue_id"], unique=False)


def downgrade() -> None:
    op.drop_index(_INDEX_NAME, table_name="matches")
    op.drop_constraint(_CONSTRAINT_NAME, "matches", type_="foreignkey")
    op.drop_column("matches", "venue_id")
