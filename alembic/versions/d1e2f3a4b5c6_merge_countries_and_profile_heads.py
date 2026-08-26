"""merge countries and user profile heads

Revision ID: d1e2f3a4b5c6
Revises: 5f6a7b8c9d0e, c8d7d9a4f2e1
Create Date: 2026-08-15 22:00:00.000000+00:00
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "d1e2f3a4b5c6"
down_revision = ("5f6a7b8c9d0e", "c8d7d9a4f2e1")
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
