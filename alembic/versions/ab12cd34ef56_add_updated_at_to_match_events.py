"""add updated_at to match_events

Revision ID: ab12cd34ef56
Revises: f4a5b6c7d8e9
Create Date: 2026-06-24 00:00:00.000000+00:00
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "ab12cd34ef56"
down_revision = "f4a5b6c7d8e9"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "match_events",
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade():
    op.drop_column("match_events", "updated_at")
