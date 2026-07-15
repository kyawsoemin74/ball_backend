"""add_team_context_columns
Revision ID: 3ea176ae5a64
Revises: a6b80ac1fcae
Create Date: 2026-07-13 06:12:56.568111+00:00
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '3ea176ae5a64'
down_revision = 'a6b80ac1fcae'
branch_labels = ('head',)
depends_on = None


def upgrade():
    op.add_column('teams', sa.Column('current_league_id', sa.Integer(), nullable=True))
    op.add_column('teams', sa.Column('current_season', sa.String(length=10), nullable=True))


def downgrade():
    op.drop_column('teams', 'current_season')
    op.drop_column('teams', 'current_league_id')
