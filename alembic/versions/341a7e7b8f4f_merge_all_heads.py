"""merge all heads
Revision ID: 341a7e7b8f4f
Revises: 0a9b265e054f, 2b6c0f1c20d7, 7d8f6a3b4c1d, 9f1a2b3c4d5e
Create Date: 2026-06-15 17:02:33.024588+00:00
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '341a7e7b8f4f'
down_revision = ('0a9b265e054f', '2b6c0f1c20d7', '7d8f6a3b4c1d', '9f1a2b3c4d5e')
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
