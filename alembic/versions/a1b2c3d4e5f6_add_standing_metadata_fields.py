"""add standing metadata fields

Revision ID: a1b2c3d4e5f6
Revises: 341a7e7b8f4f
Create Date: 2026-06-18 00:00:00.000000+00:00
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a1b2c3d4e5f6'
down_revision = '341a7e7b8f4f'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('standings', sa.Column('group_name', sa.String(length=50), nullable=True))
    op.add_column('standings', sa.Column('form', sa.String(length=20), nullable=True))
    op.add_column('standings', sa.Column('description', sa.String(length=255), nullable=True))


def downgrade():
    op.drop_column('standings', 'description')
    op.drop_column('standings', 'form')
    op.drop_column('standings', 'group_name')