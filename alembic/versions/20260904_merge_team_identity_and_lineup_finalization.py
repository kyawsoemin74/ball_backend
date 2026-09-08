"""merge team identity and lineup finalization heads

Revision ID: 20260904_merge_team_lineup_heads
Revises: 20260901_team_identity_ts, 20260904_freeze_lineup_finalization_states
"""

revision = "20260904_merge_team_lineup_heads"
down_revision = (
    "20260901_team_identity_ts",
    "20260904_freeze_lineup_finalization_states",
)
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
