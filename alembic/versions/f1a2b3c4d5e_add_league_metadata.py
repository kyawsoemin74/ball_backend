"""add league metadata columns

Revision ID: f1a2b3c4d5e
Revises: d8a7f6e5d4c3
Create Date: 2026-06-05 00:00:00.000000+00:00
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'f1a2b3c4d5e'
down_revision = 'd8a7f6e5d4c3'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('leagues', sa.Column('is_featured', sa.Boolean(), nullable=False, server_default=sa.text('false')))
    op.add_column('leagues', sa.Column('display_order', sa.Integer(), nullable=False, server_default=sa.text('999')))
    op.create_index(op.f('ix_leagues_is_featured'), 'leagues', ['is_featured'], unique=False)
    op.create_index(op.f('ix_leagues_display_order'), 'leagues', ['display_order'], unique=False)


def downgrade():
    op.drop_index(op.f('ix_leagues_display_order'), table_name='leagues')
    op.drop_index(op.f('ix_leagues_is_featured'), table_name='leagues')
    op.drop_column('leagues', 'display_order')
    op.drop_column('leagues', 'is_featured')
