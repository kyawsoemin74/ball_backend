"""add provider identity to leagues

Revision ID: 223a5b9d3e7f
Revises: c5d6e7f8a9b0
Create Date: 2026-08-17 00:00:00.000000+00:00
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "223a5b9d3e7f"
down_revision = "c5d6e7f8a9b0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("leagues", sa.Column("provider_id", sa.String(length=100), nullable=True))
    op.create_index("ix_leagues_provider_id", "leagues", ["provider_id"], unique=False)
    op.create_unique_constraint("uq_leagues_provider_provider_id", "leagues", ["provider", "provider_id"])


def downgrade() -> None:
    op.drop_constraint("uq_leagues_provider_provider_id", "leagues", type_="unique")
    op.drop_index("ix_leagues_provider_id", table_name="leagues")
    op.drop_column("leagues", "provider_id")
