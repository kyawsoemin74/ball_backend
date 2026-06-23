"""add standings constraints

Revision ID: e4f5a6b7c8d9
Revises: c9d4a8b7e6f1
Create Date: 2026-06-23 00:00:00.000000+00:00
"""

from alembic import op


# revision identifiers, used by Alembic.
revision = "e4f5a6b7c8d9"
down_revision = "c9d4a8b7e6f1"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        DELETE FROM standings
        WHERE id IN (
            SELECT id
            FROM (
                SELECT id,
                       ROW_NUMBER() OVER (
                           PARTITION BY league_id, season, team_id
                           ORDER BY updated_at DESC NULLS LAST, id DESC
                       ) AS row_num
                FROM standings
            ) ranked
            WHERE ranked.row_num > 1
        )
        """
    )
    op.create_index(
        "ix_standings_league_id_season_position",
        "standings",
        ["league_id", "season", "position"],
        unique=False,
    )
    op.create_unique_constraint(
        "uq_standings_league_id_season_team_id",
        "standings",
        ["league_id", "season", "team_id"],
    )


def downgrade():
    op.drop_constraint("uq_standings_league_id_season_team_id", "standings", type_="unique")
    op.drop_index("ix_standings_league_id_season_position", table_name="standings")