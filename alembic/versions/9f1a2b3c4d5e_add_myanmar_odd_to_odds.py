"""add myanmar_odd to odds

Revision ID: 9f1a2b3c4d5e
Revises: bafc6e79386f
Create Date: 2026-06-11 00:00:00.000000+00:00

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '9f1a2b3c4d5e'
down_revision = 'bafc6e79386f'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('odds', sa.Column('myanmar_odd', sa.String(length=20), nullable=True))


def downgrade():
    op.drop_column('odds', 'myanmar_odd')
