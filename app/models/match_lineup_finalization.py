from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, Index, Integer, JSON, String
from sqlalchemy.sql import func

from app.db import Base


class MatchLineupFinalization(Base):
    __tablename__ = "match_lineup_finalization"

    __table_args__ = (
        CheckConstraint(
            "status IN ('REQUIRED', 'RUNNING', 'RETRYABLE', 'TERMINAL', 'SUCCESS')",
            name="ck_match_lineup_finalization_status",
        ),
        CheckConstraint(
            "attempt_count >= 0",
            name="ck_match_lineup_finalization_attempt_count",
        ),
        CheckConstraint(
            "failure_category IS NULL OR failure_category IN "
            "('PROVIDER_FAILURE', 'INVALID_RESPONSE', 'MASTER_RESOLUTION_FAILURE', "
            "'ANALYTICS_FAILURE', 'DB_FAILURE', 'FLUSH_FAILURE', 'COMMIT_FAILURE', "
            "'LOCK_CONFLICT', 'SYNC_UNAVAILABLE', 'IDENTITY_BOUNDARY_VIOLATION', 'MAX_RETRY_ATTEMPTS_EXCEEDED')",
            name="ck_match_lineup_finalization_failure_category",
        ),
        CheckConstraint(
            "status NOT IN ('RETRYABLE', 'TERMINAL') OR failure_category IS NOT NULL",
            name="ck_match_lineup_finalization_retryable_failure",
        ),
        CheckConstraint(
            "(status = 'SUCCESS' AND completed_at IS NOT NULL AND failure_category IS NULL "
            "AND failure_reason IS NULL) OR "
            "(status IN ('REQUIRED', 'RUNNING', 'RETRYABLE', 'TERMINAL') AND completed_at IS NULL)",
            name="ck_match_lineup_finalization_state",
        ),
        Index(
            "ix_match_lineup_finalization_retry",
            "required_at",
            "match_id",
            postgresql_where="status IN ('REQUIRED', 'RETRYABLE')",
        ),
    )

    match_id = Column(
        Integer,
        ForeignKey("matches.local_match_id", ondelete="NO ACTION"),
        primary_key=True,
        nullable=False,
    )
    status = Column(String(20), nullable=False)
    attempt_count = Column(Integer, nullable=False, default=0, server_default="0")
    required_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    last_attempted_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    failure_category = Column(String(40), nullable=True)
    failure_reason = Column(String(500), nullable=True)
    failure_diagnostics = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())