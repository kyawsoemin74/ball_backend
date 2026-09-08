"""add missing news image URL column

Revision ID: 20260907_add_news_image_url
Revises: 20260904_merge_team_lineup_heads
"""

from alembic import op
import sqlalchemy as sa


revision = "20260907_add_news_image_url"
down_revision = "20260904_merge_team_lineup_heads"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("news")}
    if "image_url" not in columns:
        op.add_column("news", sa.Column("image_url", sa.String(length=500), nullable=True))


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("news")}
    if "image_url" in columns:
        op.drop_column("news", "image_url")
