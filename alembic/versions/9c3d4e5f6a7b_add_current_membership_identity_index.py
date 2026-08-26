"""prevent duplicate current Player-Team observations

Revision ID: 9c3d4e5f6a7b
Revises: 9b2c3d4e5f6a
"""

from alembic import op
import sqlalchemy as sa


revision = "9c3d4e5f6a7b"
down_revision = "9b2c3d4e5f6a"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index(
        "uq_player_team_membership_current_identity",
        "player_team_memberships",
        ["provider", "player_id", "team_id"],
        unique=True,
        postgresql_where=sa.text("valid_from IS NULL"),
    )


def downgrade():
    op.drop_index(
        "uq_player_team_membership_current_identity",
        table_name="player_team_memberships",
    )