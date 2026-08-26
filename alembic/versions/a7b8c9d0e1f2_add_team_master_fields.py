"""add team master fields (provider, provider_id, country_id)

Revision ID: a7b8c9d0e1f2
Revises: f7a8b9c0d1e2
Create Date: 2026-08-15 22:45:00.000000+00:00
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "a7b8c9d0e1f2"
down_revision = "f7a8b9c0d1e2"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("teams", sa.Column("provider", sa.String(length=50), nullable=False, server_default=sa.text("'api-football'")))
    op.add_column("teams", sa.Column("provider_id", sa.String(length=100), nullable=True))
    op.add_column("teams", sa.Column("country_id", sa.Integer(), nullable=True))

    op.create_foreign_key(
        "fk_teams_country_id",
        "teams",
        "countries",
        ["country_id"],
        ["country_id"],
    )
    op.create_index("ix_teams_country_id", "teams", ["country_id"], unique=False)
    op.create_index("ix_teams_provider_id", "teams", ["provider_id"], unique=False)
    op.create_unique_constraint("uq_teams_provider_provider_id", "teams", ["provider", "provider_id"])


def downgrade():
    op.drop_constraint("uq_teams_provider_provider_id", "teams", type_="unique")
    op.drop_index("ix_teams_provider_id", table_name="teams")
    op.drop_index("ix_teams_country_id", table_name="teams")
    op.drop_constraint("fk_teams_country_id", "teams", type_="foreignkey")
    op.drop_column("teams", "country_id")
    op.drop_column("teams", "provider_id")
    op.drop_column("teams", "provider")
