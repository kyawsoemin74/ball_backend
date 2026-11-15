"""Add durable fast-recheck progress to Match finalization.

Revision ID: 20261114_fast_final_rechecks
Revises: 20261030_h2h_local_sides
"""

from alembic import op
import sqlalchemy as sa


revision = "20261114_fast_final_rechecks"
down_revision = "20261030_h2h_local_sides"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "match_finalization",
        sa.Column(
            "fast_recheck_count",
            sa.Integer(),
            server_default="-1",
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_match_finalization_fast_recheck_count",
        "match_finalization",
        "fast_recheck_count BETWEEN -1 AND 3",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_match_finalization_fast_recheck_count",
        "match_finalization",
        type_="check",
    )
    op.drop_column("match_finalization", "fast_recheck_count")
