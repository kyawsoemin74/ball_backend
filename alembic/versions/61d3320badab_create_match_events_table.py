"""create match_events table

Revision ID: 61d3320badab
Revises: bafc6e79386f
Create Date: 2026-05-31 07:08:14.373984+00:00

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '61d3320badab'
down_revision = 'bafc6e79386f'
branch_labels = None
depends_on = None


def upgrade():
    # Create match_events table
    op.create_table(
        'match_events',
        sa.Column('id', sa.Integer(), nullable=False, autoincrement=True),
        sa.Column('match_id', sa.Integer(), nullable=False),
        sa.Column('time_elapsed', sa.Integer(), nullable=False),
        sa.Column('time_extra', sa.Integer(), nullable=True),
        sa.Column('team_id', sa.Integer(), nullable=False),
        sa.Column('team_name', sa.String(length=255), nullable=True),
        sa.Column('player_id', sa.Integer(), nullable=True),
        sa.Column('player_name', sa.String(length=255), nullable=True),
        sa.Column('assist_id', sa.Integer(), nullable=True),
        sa.Column('assist_name', sa.String(length=255), nullable=True),
        sa.Column('type', sa.String(length=50), nullable=False),
        sa.Column('detail', sa.String(length=255), nullable=True),
        sa.Column('comments', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['match_id'], ['matches.fixture_id'], name=op.f('fk_match_events_match_id_matches')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_match_events')),
    )
    op.create_index(op.f('ix_match_events_match_id'), 'match_events', ['match_id'], unique=False)


def downgrade():
    # Drop index
    op.drop_index(op.f('ix_match_events_match_id'), table_name='match_events')
    
    # Drop foreign key constraint
    op.drop_constraint(op.f('fk_match_events_match_id_matches'), 'match_events', type_='foreignkey')
    
    # Drop table
    op.drop_table('match_events')
