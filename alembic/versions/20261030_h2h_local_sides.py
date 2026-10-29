"""Add local fixture-side identities to H2H analytics.

Revision ID: 20261030_h2h_local_sides
Revises: 20261020_h2h_sync_work
"""

import logging

from alembic import context, op
import sqlalchemy as sa


revision = "20261030_h2h_local_sides"
down_revision = "20261020_h2h_sync_work"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")


def upgrade():
    op.add_column(
        "analytics_h2h_snapshots",
        sa.Column("home_local_team_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "analytics_h2h_snapshots",
        sa.Column("away_local_team_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_analytics_h2h_home_local_team",
        "analytics_h2h_snapshots",
        "teams",
        ["home_local_team_id"],
        ["team_id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_analytics_h2h_away_local_team",
        "analytics_h2h_snapshots",
        "teams",
        ["away_local_team_id"],
        ["team_id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_analytics_h2h_local_sides_match_pair",
        "analytics_h2h_snapshots",
        "(home_local_team_id IS NULL AND away_local_team_id IS NULL) OR "
        "(home_local_team_id IS NOT NULL AND away_local_team_id IS NOT NULL "
        "AND home_local_team_id <> away_local_team_id "
        "AND LEAST(home_local_team_id, away_local_team_id) = team_low_id "
        "AND GREATEST(home_local_team_id, away_local_team_id) = team_high_id)",
    )
    op.execute(
        sa.text(
            """
            UPDATE analytics_h2h_snapshots AS snapshot
            SET home_local_team_id = home_team.team_id,
                away_local_team_id = away_team.team_id
            FROM teams AS home_team, teams AS away_team
            WHERE home_team.provider = snapshot.source_provider
              AND home_team.provider_id = snapshot.home_provider_team_id::text
              AND away_team.provider = snapshot.source_provider
              AND away_team.provider_id = snapshot.away_provider_team_id::text
              AND home_team.team_id <> away_team.team_id
              AND LEAST(home_team.team_id, away_team.team_id) = snapshot.team_low_id
              AND GREATEST(home_team.team_id, away_team.team_id) = snapshot.team_high_id
            """
        )
    )

    if not context.is_offline_mode():
        counts = op.get_bind().execute(
            sa.text(
                """
                SELECT
                    COUNT(*) AS total,
                    COUNT(*) FILTER (
                        WHERE snapshot.home_local_team_id IS NOT NULL
                          AND snapshot.away_local_team_id IS NOT NULL
                    ) AS resolved,
                    COUNT(*) FILTER (
                        WHERE snapshot.home_local_team_id IS NULL
                          OR snapshot.away_local_team_id IS NULL
                    ) AS unresolved,
                    COUNT(*) FILTER (
                        WHERE home_team.team_id IS NOT NULL
                          AND away_team.team_id IS NOT NULL
                          AND (
                              home_team.team_id = away_team.team_id
                              OR LEAST(home_team.team_id, away_team.team_id) <> snapshot.team_low_id
                              OR GREATEST(home_team.team_id, away_team.team_id) <> snapshot.team_high_id
                          )
                    ) AS pair_mismatch
                FROM analytics_h2h_snapshots AS snapshot
                LEFT JOIN teams AS home_team
                  ON home_team.provider = snapshot.source_provider
                 AND home_team.provider_id = snapshot.home_provider_team_id::text
                LEFT JOIN teams AS away_team
                  ON away_team.provider = snapshot.source_provider
                 AND away_team.provider_id = snapshot.away_provider_team_id::text
                """
            )
        ).mappings().one()
        logger.info(
            "H2H local-side backfill: total=%s resolved=%s unresolved=%s pair_mismatch=%s",
            counts["total"],
            counts["resolved"],
            counts["unresolved"],
            counts["pair_mismatch"],
        )


def downgrade():
    op.drop_constraint(
        "ck_analytics_h2h_local_sides_match_pair",
        "analytics_h2h_snapshots",
        type_="check",
    )
    op.drop_constraint(
        "fk_analytics_h2h_away_local_team",
        "analytics_h2h_snapshots",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_analytics_h2h_home_local_team",
        "analytics_h2h_snapshots",
        type_="foreignkey",
    )
    op.drop_column("analytics_h2h_snapshots", "away_local_team_id")
    op.drop_column("analytics_h2h_snapshots", "home_local_team_id")
