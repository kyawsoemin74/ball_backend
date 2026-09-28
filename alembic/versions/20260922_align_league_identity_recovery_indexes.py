"""align League identity recovery indexes with ORM metadata

Revision ID: 20260922_align_recovery_indexes
Revises: 20260922_league_identity_recovery
"""

from alembic import op


revision = "20260922_align_recovery_indexes"
down_revision = "20260922_league_identity_recovery"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index(
        "ix_league_identity_recovery_league_id",
        table_name="league_identity_recovery",
    )


def downgrade() -> None:
    op.create_index(
        "ix_league_identity_recovery_league_id",
        "league_identity_recovery",
        ["league_id"],
        unique=False,
    )