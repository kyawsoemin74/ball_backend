"""add home and away team id to matches
Revision ID: 5f1a2b3c4d5e
Revises: 2c248ce5b79b
Create Date: 2026-05-24 00:00:00.000000+00:00
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '5f1a2b3c4d5e'
down_revision = '2c248ce5b79b'
branch_labels = None
depends_on = None


def upgrade():
    # Add columns
    op.add_column('matches', sa.Column('home_team_id', sa.Integer(), nullable=True))
    op.add_column('matches', sa.Column('away_team_id', sa.Integer(), nullable=True))
    # Create foreign key constraints to teams.team_id
    op.create_foreign_key('fk_matches_home_team', 'matches', 'teams', ['home_team_id'], ['team_id'])
    op.create_foreign_key('fk_matches_away_team', 'matches', 'teams', ['away_team_id'], ['team_id'])


def downgrade():
    # Drop foreign keys then columns
    op.drop_constraint('fk_matches_away_team', 'matches', type_='foreignkey')
    op.drop_constraint('fk_matches_home_team', 'matches', type_='foreignkey')
    op.drop_column('matches', 'away_team_id')
    op.drop_column('matches', 'home_team_id')
