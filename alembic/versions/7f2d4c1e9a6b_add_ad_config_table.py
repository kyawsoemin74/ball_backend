"""add ad config table

Revision ID: 7f2d4c1e9a6b
Revises: 3ea176ae5a64
Create Date: 2026-07-28 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "7f2d4c1e9a6b"
down_revision = "3ea176ae5a64"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "ad_configs",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("banner_android", sa.String(length=255), nullable=True),
        sa.Column("banner_ios", sa.String(length=255), nullable=True),
        sa.Column("interstitial_android", sa.String(length=255), nullable=True),
        sa.Column("interstitial_ios", sa.String(length=255), nullable=True),
        sa.Column("rewarded_android", sa.String(length=255), nullable=True),
        sa.Column("rewarded_ios", sa.String(length=255), nullable=True),
        sa.Column("app_open_android", sa.String(length=255), nullable=True),
        sa.Column("app_open_ios", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(op.f("ix_ad_configs_id"), "ad_configs", ["id"], unique=False)


def downgrade():
    op.drop_index(op.f("ix_ad_configs_id"), table_name="ad_configs")
    op.drop_table("ad_configs")
