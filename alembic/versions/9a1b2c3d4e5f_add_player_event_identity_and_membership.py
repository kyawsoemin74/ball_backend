"""add player event identity fields and membership table

Revision ID: 9a1b2c3d4e5f
Revises: 223a5b9d3e7f
"""

from alembic import op
import sqlalchemy as sa


revision = "9a1b2c3d4e5f"
down_revision = "223a5b9d3e7f"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("match_events", sa.Column("provider_player_id", sa.String(length=100), nullable=True))
    op.add_column("match_events", sa.Column("provider_assist_id", sa.String(length=100), nullable=True))

    op.create_table(
        "player_team_memberships",
        sa.Column("player_team_membership_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("player_id", sa.Integer(), nullable=False),
        sa.Column("team_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=50), server_default="api-football", nullable=False),
        sa.Column("provider_team_id", sa.String(length=100), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_current", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("source", sa.String(length=50), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["player_id"], ["players.player_id"]),
        sa.ForeignKeyConstraint(["team_id"], ["teams.team_id"]),
        sa.PrimaryKeyConstraint("player_team_membership_id"),
        sa.UniqueConstraint(
            "provider",
            "player_id",
            "team_id",
            "valid_from",
            name="uq_player_team_membership_identity",
        ),
    )
    op.create_index(
        "ix_player_team_memberships_player_current",
        "player_team_memberships",
        ["player_id", "is_current"],
    )
    op.create_index("ix_player_team_memberships_team", "player_team_memberships", ["team_id"])
    op.create_index(
        "ix_player_team_memberships_provider_team",
        "player_team_memberships",
        ["provider", "provider_team_id"],
    )


def downgrade():
    op.drop_index("ix_player_team_memberships_provider_team", table_name="player_team_memberships")
    op.drop_index("ix_player_team_memberships_team", table_name="player_team_memberships")
    op.drop_index("ix_player_team_memberships_player_current", table_name="player_team_memberships")
    op.drop_table("player_team_memberships")
    op.drop_column("match_events", "provider_assist_id")
    op.drop_column("match_events", "provider_player_id")