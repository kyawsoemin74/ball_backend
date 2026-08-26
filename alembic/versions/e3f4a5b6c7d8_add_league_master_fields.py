"""add league master fields (provider, type, national, country_id)

Revision ID: e3f4a5b6c7d8
Revises: d1e2f3a4b5c6
Create Date: 2026-08-15 22:30:00.000000+00:00
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "e3f4a5b6c7d8"
down_revision = "d1e2f3a4b5c6"
branch_labels = None
depends_on = None


def upgrade():
    # Add provider column (API-Football provider identifier)
    op.add_column(
        "leagues",
        sa.Column("provider", sa.String(length=50), nullable=False, server_default=sa.text("'api-football'"))
    )
    
    # Add type column (league type: domestic, club, international)
    op.add_column(
        "leagues",
        sa.Column("type", sa.String(length=50), nullable=True)
    )
    
    # Add national column (is this a national team league)
    op.add_column(
        "leagues",
        sa.Column("national", sa.Boolean(), nullable=True)
    )
    
    # Add country_id column (foreign key to countries table)
    op.add_column(
        "leagues",
        sa.Column("country_id", sa.Integer(), nullable=True)
    )
    
    # Create foreign key constraint
    op.create_foreign_key(
        "fk_leagues_country_id",
        "leagues",
        "countries",
        ["country_id"],
        ["country_id"],
    )
    
    # Create index on country_id
    op.create_index("ix_leagues_country_id", "leagues", ["country_id"], unique=False)


def downgrade():
    # Remove index
    op.drop_index("ix_leagues_country_id", table_name="leagues")
    
    # Remove foreign key
    op.drop_constraint("fk_leagues_country_id", "leagues", type_="foreignkey")
    
    # Remove columns
    op.drop_column("leagues", "country_id")
    op.drop_column("leagues", "national")
    op.drop_column("leagues", "type")
    op.drop_column("leagues", "provider")
