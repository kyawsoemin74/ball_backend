"""freeze lineup finalization lifecycle states"""

from alembic import op
import sqlalchemy as sa


revision = "20260904_freeze_lineup_finalization_states"
down_revision = "20260825_lineup_finalization"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column(
        "alembic_version",
        "version_num",
        existing_type=sa.String(length=32),
        type_=sa.String(length=64),
        existing_nullable=False,
    )
    op.add_column("match_lineup_finalization", sa.Column("failure_diagnostics", sa.JSON(), nullable=True))
    op.drop_constraint("ck_match_lineup_finalization_failure_category", "match_lineup_finalization", type_="check")
    op.create_check_constraint(
        "ck_match_lineup_finalization_failure_category",
        "match_lineup_finalization",
        "failure_category IS NULL OR failure_category IN ('PROVIDER_FAILURE', 'INVALID_RESPONSE', 'MASTER_RESOLUTION_FAILURE', 'ANALYTICS_FAILURE', 'DB_FAILURE', 'FLUSH_FAILURE', 'COMMIT_FAILURE', 'LOCK_CONFLICT', 'SYNC_UNAVAILABLE', 'IDENTITY_BOUNDARY_VIOLATION', 'MAX_RETRY_ATTEMPTS_EXCEEDED')",
    )
    op.drop_constraint("ck_match_lineup_finalization_status", "match_lineup_finalization", type_="check")
    op.create_check_constraint(
        "ck_match_lineup_finalization_status",
        "match_lineup_finalization",
        "status IN ('REQUIRED', 'RUNNING', 'RETRYABLE', 'TERMINAL', 'SUCCESS')",
    )
    op.drop_constraint("ck_match_lineup_finalization_retryable_failure", "match_lineup_finalization", type_="check")
    op.create_check_constraint(
        "ck_match_lineup_finalization_retryable_failure",
        "match_lineup_finalization",
        "status NOT IN ('RETRYABLE', 'TERMINAL') OR failure_category IS NOT NULL",
    )
    op.drop_constraint("ck_match_lineup_finalization_state", "match_lineup_finalization", type_="check")
    op.create_check_constraint(
        "ck_match_lineup_finalization_state",
        "match_lineup_finalization",
        "(status = 'SUCCESS' AND completed_at IS NOT NULL AND failure_category IS NULL AND failure_reason IS NULL) OR (status IN ('REQUIRED', 'RUNNING', 'RETRYABLE', 'TERMINAL') AND completed_at IS NULL)",
    )


def downgrade():
    op.drop_column("match_lineup_finalization", "failure_diagnostics")
    op.drop_constraint("ck_match_lineup_finalization_state", "match_lineup_finalization", type_="check")
    op.drop_constraint("ck_match_lineup_finalization_retryable_failure", "match_lineup_finalization", type_="check")
    op.drop_constraint("ck_match_lineup_finalization_status", "match_lineup_finalization", type_="check")
    op.drop_constraint("ck_match_lineup_finalization_failure_category", "match_lineup_finalization", type_="check")
    op.create_check_constraint("ck_match_lineup_finalization_failure_category", "match_lineup_finalization", "failure_category IS NULL OR failure_category IN ('PROVIDER_FAILURE', 'INVALID_RESPONSE', 'MASTER_RESOLUTION_FAILURE', 'ANALYTICS_FAILURE', 'DB_FAILURE', 'FLUSH_FAILURE', 'COMMIT_FAILURE', 'LOCK_CONFLICT')")
    op.create_check_constraint("ck_match_lineup_finalization_status", "match_lineup_finalization", "status IN ('REQUIRED', 'RETRYABLE', 'SUCCESS')")
    op.create_check_constraint("ck_match_lineup_finalization_retryable_failure", "match_lineup_finalization", "status <> 'RETRYABLE' OR failure_category IS NOT NULL")
    op.create_check_constraint("ck_match_lineup_finalization_state", "match_lineup_finalization", "(status = 'SUCCESS' AND completed_at IS NOT NULL AND failure_category IS NULL AND failure_reason IS NULL) OR (status IN ('REQUIRED', 'RETRYABLE') AND completed_at IS NULL)")
    op.alter_column(
        "alembic_version",
        "version_num",
        existing_type=sa.String(length=64),
        type_=sa.String(length=32),
        existing_nullable=False,
    )