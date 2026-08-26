"""add Player Master foreign keys to match events

Revision ID: 9b2c3d4e5f6a
Revises: 9a1b2c3d4e5f
"""

from alembic import op


revision = "9b2c3d4e5f6a"
down_revision = "9a1b2c3d4e5f"
branch_labels = None
depends_on = None


def upgrade():
    op.create_foreign_key(
        "fk_match_events_player_id_players",
        "match_events",
        "players",
        ["player_id"],
        ["player_id"],
    )
    op.create_foreign_key(
        "fk_match_events_assist_id_players",
        "match_events",
        "players",
        ["assist_id"],
        ["player_id"],
    )


def downgrade():
    op.drop_constraint("fk_match_events_assist_id_players", "match_events", type_="foreignkey")
    op.drop_constraint("fk_match_events_player_id_players", "match_events", type_="foreignkey")