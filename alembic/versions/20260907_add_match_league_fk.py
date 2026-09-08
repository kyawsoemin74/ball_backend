"""add missing match league foreign key

Revision ID: 20260907_add_match_league_fk
Revises: 20260907_add_news_image_url
"""

from alembic import op
import sqlalchemy as sa


revision = "20260907_add_match_league_fk"
down_revision = "20260907_add_news_image_url"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {
        constraint.get("name")
        for constraint in inspector.get_foreign_keys("matches")
    }
    if "fk_matches_league_id_leagues" not in existing:
        op.create_foreign_key(
            "fk_matches_league_id_leagues",
            "matches",
            "leagues",
            ["league_id"],
            ["league_id"],
        )


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {
        constraint.get("name")
        for constraint in inspector.get_foreign_keys("matches")
    }
    if "fk_matches_league_id_leagues" in existing:
        op.drop_constraint("fk_matches_league_id_leagues", "matches", type_="foreignkey")
