"""add player master table
Revision ID: 5d5d1902d0d5
Revises: a7b8c9d0e1f2
Create Date: 2026-08-15 17:13:32.288111+00:00
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '5d5d1902d0d5'
down_revision = 'a7b8c9d0e1f2'
branch_labels = None
depends_on = None


def upgrade():
    # Create players table
    op.create_table(
        'players',
        sa.Column('player_id', sa.Integer(), nullable=False, autoincrement=True),
        sa.Column('provider', sa.String(length=50), nullable=False, server_default='api-football'),
        sa.Column('provider_id', sa.String(length=100), nullable=False),
        sa.Column('first_name', sa.String(length=100), nullable=True),
        sa.Column('last_name', sa.String(length=100), nullable=True),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('nationality', sa.String(length=100), nullable=True),
        sa.Column('birth_date', sa.DateTime(), nullable=True),
        sa.Column('birth_place', sa.String(length=255), nullable=True),
        sa.Column('birth_country', sa.String(length=100), nullable=True),
        sa.Column('height', sa.Integer(), nullable=True),
        sa.Column('weight', sa.Integer(), nullable=True),
        sa.Column('position', sa.String(length=50), nullable=True),
        sa.Column('preferred_foot', sa.String(length=20), nullable=True),
        sa.Column('photo', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now(), onupdate=sa.func.now()),
        sa.PrimaryKeyConstraint('player_id'),
        sa.UniqueConstraint('provider', 'provider_id', name='uq_players_provider_provider_id'),
    )
    
    # Create indexes
    op.create_index('ix_players_provider_id', 'players', ['provider_id'], unique=False)
    op.create_index('ix_players_name', 'players', ['name'], unique=False)
    op.create_index('ix_players_player_id', 'players', ['player_id'], unique=False)


def downgrade():
    # Drop indexes
    op.drop_index('ix_players_player_id', table_name='players')
    op.drop_index('ix_players_name', table_name='players')
    op.drop_index('ix_players_provider_id', table_name='players')
    
    # Drop table
    op.drop_table('players')

