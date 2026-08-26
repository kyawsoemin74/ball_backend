"""add current Team to Coach relationship

Revision ID: 20260824_team_coach
Revises: 20260823_match_referee
Create Date: 2026-08-24 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "20260824_team_coach"
down_revision = "20260823_match_referee"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("teams", sa.Column("coach_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_teams_coach_id",
        "teams",
        "coaches",
        ["coach_id"],
        ["coach_id"],
        ondelete="RESTRICT",
    )
    op.create_unique_constraint("uq_teams_coach_id", "teams", ["coach_id"])


def downgrade() -> None:
    op.drop_constraint("uq_teams_coach_id", "teams", type_="unique")
    op.drop_constraint("fk_teams_coach_id", "teams", type_="foreignkey")
    op.drop_column("teams", "coach_id")