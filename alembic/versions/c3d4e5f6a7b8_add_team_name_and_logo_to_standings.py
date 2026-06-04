"""add_team_name_and_logo_to_standings
Revision ID: c3d4e5f6a7b8
Revises: bafc6e79386f
Create Date: 2026-06-04 00:00:00.000000+00:00
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'c3d4e5f6a7b8'
down_revision = "61d3320badab"
branch_labels = None
depends_on = None


def upgrade():
    # Add new nullable columns for team metadata used by standings sync
    op.add_column('standings', sa.Column('team_name', sa.String(length=255), nullable=True))
    op.add_column('standings', sa.Column('team_logo', sa.String(length=1024), nullable=True))


def downgrade():
    op.drop_column('standings', 'team_logo')
    op.drop_column('standings', 'team_name')
