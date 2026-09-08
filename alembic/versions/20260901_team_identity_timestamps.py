"""correct team identity and timestamp constraints

Revision ID: 20260901_team_identity_ts
Revises: 20260901_standing_ts
Create Date: 2026-09-01 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "20260901_team_identity_ts"
down_revision = "20260901_standing_ts"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    unique_constraints = {
        constraint["name"]
        for constraint in inspector.get_unique_constraints("teams")
    }
    if "uq_teams_name" in unique_constraints:
        op.drop_constraint("uq_teams_name", "teams", type_="unique")

    op.execute(
        """
        UPDATE teams
        SET updated_at = created_at
        WHERE updated_at IS NULL
        """
    )
    op.alter_column(
        "teams",
        "updated_at",
        existing_type=sa.DateTime(timezone=True),
        server_default=sa.func.now(),
        nullable=False,
    )


def downgrade():
    op.alter_column(
        "teams",
        "updated_at",
        existing_type=sa.DateTime(timezone=True),
        server_default=None,
        nullable=True,
    )

    bind = op.get_bind()
    inspector = sa.inspect(bind)
    unique_constraints = {
        constraint["name"]
        for constraint in inspector.get_unique_constraints("teams")
    }
    if "uq_teams_name" not in unique_constraints:
        op.create_unique_constraint("uq_teams_name", "teams", ["name"])
