"""create league_seasons table

Revision ID: f7a8b9c0d1e2
Revises: e3f4a5b6c7d8
Create Date: 2026-08-15 00:00:00.000000+00:00
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "f7a8b9c0d1e2"
down_revision = "e3f4a5b6c7d8"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "league_seasons",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("league_id", sa.Integer(), nullable=False),
        sa.Column("season", sa.String(length=20), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=True),
        sa.Column("provider_id", sa.String(length=100), nullable=True),
        sa.Column("start_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("end_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("current", sa.Boolean(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(op.f("ix_league_seasons_id"), "league_seasons", ["id"], unique=False)
    op.create_index(op.f("ix_league_seasons_league_id"), "league_seasons", ["league_id"], unique=False)
    op.create_index(op.f("ix_league_seasons_season"), "league_seasons", ["season"], unique=False)
    op.create_unique_constraint(
        "uq_league_seasons_league_id_season",
        "league_seasons",
        ["league_id", "season"],
    )
    op.create_foreign_key(
        "fk_league_seasons_league_id",
        "league_seasons",
        "leagues",
        ["league_id"],
        ["league_id"],
    )


def downgrade():
    op.drop_constraint("fk_league_seasons_league_id", "league_seasons", type_="foreignkey")
    op.drop_constraint("uq_league_seasons_league_id_season", "league_seasons", type_="unique")
    op.drop_index(op.f("ix_league_seasons_season"), table_name="league_seasons")
    op.drop_index(op.f("ix_league_seasons_league_id"), table_name="league_seasons")
    op.drop_index(op.f("ix_league_seasons_id"), table_name="league_seasons")
    op.drop_table("league_seasons")
