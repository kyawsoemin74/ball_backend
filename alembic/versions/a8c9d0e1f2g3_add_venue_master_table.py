"""add venue master table.

Revision ID: a8c9d0e1f2g3
Revises: 5d5d1902d0d5
Create Date: 2026-08-15 23:50:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a8c9d0e1f2g3'
down_revision = '5d5d1902d0d5'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create venues table
    op.create_table(
        'venues',
        sa.Column('venue_id', sa.Integer(), nullable=False),
        sa.Column('provider', sa.String(length=50), server_default='api-football', nullable=False),
        sa.Column('provider_id', sa.String(length=100), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('city', sa.String(length=255), nullable=True),
        sa.Column('country', sa.String(length=100), nullable=True),
        sa.Column('country_code', sa.String(length=2), nullable=True),
        sa.Column('capacity', sa.Integer(), nullable=True),
        sa.Column('surface', sa.String(length=50), nullable=True),
        sa.Column('image', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('venue_id'),
        sa.UniqueConstraint('provider', 'provider_id', name='uq_venues_provider_provider_id'),
    )
    
    # Create indexes
    op.create_index('ix_venues_provider_id', 'venues', ['provider_id'], unique=False)
    op.create_index('ix_venues_name', 'venues', ['name'], unique=False)
    op.create_index('ix_venues_venue_id', 'venues', ['venue_id'], unique=False)


def downgrade() -> None:
    # Drop indexes
    op.drop_index('ix_venues_venue_id', table_name='venues')
    op.drop_index('ix_venues_name', table_name='venues')
    op.drop_index('ix_venues_provider_id', table_name='venues')
    
    # Drop table
    op.drop_table('venues')
