"""scope standing rows by canonical LeagueSeason identity

Revision ID: 20260926_standing_ls_identity
Revises: 20260922_align_recovery_indexes
"""

from alembic import op
import sqlalchemy as sa


revision = "20260926_standing_ls_identity"
down_revision = "20260922_align_recovery_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "standings",
        sa.Column("league_season_id", sa.Integer(), nullable=True),
    )
    op.execute(
        sa.text(
            """
            UPDATE standings AS standing
            SET league_season_id = league_season.id
            FROM league_seasons AS league_season
            WHERE league_season.league_id = standing.league_id
              AND league_season.season = standing.season
            """
        )
    )

    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM standings WHERE league_season_id IS NULL) THEN
                RAISE EXCEPTION 'Cannot migrate standings: rows have no exact LeagueSeason identity';
            END IF;
        END
        $$;
        """
    )

    op.drop_index("ix_standings_league_id_season_position", table_name="standings")
    op.drop_constraint("uq_standings_league_id_season_team_id", "standings", type_="unique")
    op.drop_constraint("fk_standings_league_id_leagues", "standings", type_="foreignkey")
    op.drop_column("standings", "league_id")
    op.drop_column("standings", "season")

    op.alter_column("standings", "league_season_id", nullable=False)
    op.create_foreign_key(
        "fk_standings_league_season_id_league_seasons",
        "standings",
        "league_seasons",
        ["league_season_id"],
        ["id"],
    )
    op.create_index(
        "ix_standings_league_season_position",
        "standings",
        ["league_season_id", "position"],
        unique=False,
    )
    op.create_unique_constraint(
        "uq_standings_league_season_team_id",
        "standings",
        ["league_season_id", "team_id"],
    )


def downgrade() -> None:
    op.add_column("standings", sa.Column("league_id", sa.Integer(), nullable=True))
    op.add_column("standings", sa.Column("season", sa.String(length=10), nullable=True))
    op.execute(
        sa.text(
            """
            UPDATE standings AS standing
            SET league_id = league_season.league_id,
                season = league_season.season
            FROM league_seasons AS league_season
            WHERE league_season.id = standing.league_season_id
            """
        )
    )
    op.alter_column("standings", "league_id", nullable=False)
    op.alter_column("standings", "season", nullable=False)

    op.drop_constraint("uq_standings_league_season_team_id", "standings", type_="unique")
    op.drop_index("ix_standings_league_season_position", table_name="standings")
    op.drop_constraint(
        "fk_standings_league_season_id_league_seasons",
        "standings",
        type_="foreignkey",
    )
    op.drop_column("standings", "league_season_id")

    op.create_foreign_key(
        "fk_standings_league_id_leagues",
        "standings",
        "leagues",
        ["league_id"],
        ["league_id"],
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
