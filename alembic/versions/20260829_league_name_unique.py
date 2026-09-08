"""correct league identity constraints

Revision ID: 20260829_league_name_unique
Revises: 20260828_google_id
Create Date: 2026-08-29 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "20260829_league_name_unique"
down_revision = "20260828_google_id"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    unique_constraints = {
        constraint["name"]
        for constraint in inspector.get_unique_constraints("leagues")
    }
    if "uq_leagues_name" in unique_constraints:
        op.drop_constraint("uq_leagues_name", "leagues", type_="unique")

    unique_constraints = {
        constraint["name"]
        for constraint in inspector.get_unique_constraints("leagues")
    }
    if "uq_leagues_provider_provider_id" not in unique_constraints:
        op.create_unique_constraint(
            "uq_leagues_provider_provider_id",
            "leagues",
            ["provider", "provider_id"],
        )


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    unique_constraints = {
        constraint["name"]
        for constraint in inspector.get_unique_constraints("leagues")
    }
    if "uq_leagues_provider_provider_id" in unique_constraints:
        op.drop_constraint(
            "uq_leagues_provider_provider_id",
            "leagues",
            type_="unique",
        )

    unique_constraints = {
        constraint["name"]
        for constraint in inspector.get_unique_constraints("leagues")
    }
    if "uq_leagues_name" not in unique_constraints:
        op.create_unique_constraint("uq_leagues_name", "leagues", ["name"])
