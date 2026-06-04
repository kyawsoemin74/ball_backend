"""remove_unique_constraint_from_teams_name
Revision ID: d8a7f6e5d4c3
Revises: bafc6e79386f
Create Date: 2026-06-04 00:00:00.000000+00:00
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'd8a7f6e5d4c3'
down_revision = 'bafc6e79386f'
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint('teams_name_key', 'teams', type_='unique')


def downgrade():
    op.create_unique_constraint('teams_name_key', 'teams', ['name'])
