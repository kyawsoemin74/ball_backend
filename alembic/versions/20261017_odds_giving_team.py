"""Add derived AH giving-team snapshot fields.

Revision ID: 20261017_odds_giving_team
Revises: 20261009_odds_retry_state
"""

from alembic import op
import sqlalchemy as sa


revision = "20261017_odds_giving_team"
down_revision = "20261009_odds_retry_state"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("odds", sa.Column("giving_team", sa.String(length=4), nullable=True))
    op.create_check_constraint(
        "ck_odds_giving_team",
        "odds",
        "giving_team IS NULL OR giving_team IN ('HOME', 'AWAY')",
    )

    op.add_column(
        "analytics_match_odds_snapshots",
        sa.Column("giving_team", sa.String(length=4), nullable=True),
    )
    op.create_check_constraint(
        "ck_analytics_odds_giving_team",
        "analytics_match_odds_snapshots",
        "giving_team IS NULL OR giving_team IN ('HOME', 'AWAY')",
    )


def downgrade():
    op.drop_constraint(
        "ck_analytics_odds_giving_team",
        "analytics_match_odds_snapshots",
        type_="check",
    )
    op.drop_column("analytics_match_odds_snapshots", "giving_team")
    op.drop_constraint("ck_odds_giving_team", "odds", type_="check")
    op.drop_column("odds", "giving_team")
