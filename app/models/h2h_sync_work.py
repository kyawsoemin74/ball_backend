from sqlalchemy import Boolean, CheckConstraint, Column, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func, text

from app.db import Base


class H2HSyncWork(Base):
    __tablename__ = "h2h_sync_work"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING', 'PROCESSING', 'SUCCESS', 'EMPTY', 'FAILED')",
            name="ck_h2h_sync_work_status",
        ),
        CheckConstraint("attempt_count >= 0", name="ck_h2h_sync_work_attempt_count"),
        CheckConstraint(
            "status != 'PROCESSING' OR "
            "(claimed_at IS NOT NULL AND lease_expires_at IS NOT NULL AND claim_token IS NOT NULL)",
            name="ck_h2h_sync_work_processing_lease",
        ),
        CheckConstraint(
            "status != 'FAILED' OR retryable = FALSE OR next_retry_at IS NOT NULL",
            name="ck_h2h_sync_work_failed_retry",
        ),
        Index(
            "ix_h2h_sync_work_due",
            "status",
            "next_retry_at",
            "match_time",
            "local_match_id",
            postgresql_where=text("status IN ('PENDING', 'FAILED')"),
        ),
        Index(
            "ix_h2h_sync_work_expired_lease",
            "lease_expires_at",
            postgresql_where=text("status = 'PROCESSING'"),
        ),
        Index("ix_h2h_sync_work_pair_status", "pair_key", "status"),
    )

    local_match_id = Column(
        Integer,
        ForeignKey("matches.local_match_id", ondelete="RESTRICT"),
        primary_key=True,
        nullable=False,
    )
    pair_key = Column(String(50), nullable=False)
    match_time = Column(DateTime(timezone=True), nullable=False)
    status = Column(String(16), nullable=False, server_default="PENDING")
    attempt_count = Column(Integer, nullable=False, server_default="0")
    retryable = Column(Boolean, nullable=False, server_default="true")
    last_attempt_at = Column(DateTime(timezone=True), nullable=True)
    next_retry_at = Column(DateTime(timezone=True), nullable=True)
    last_error_code = Column(String(100), nullable=True)
    last_error = Column(Text, nullable=True)
    claimed_at = Column(DateTime(timezone=True), nullable=True)
    lease_expires_at = Column(DateTime(timezone=True), nullable=True)
    claim_token = Column(UUID(as_uuid=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    completed_at = Column(DateTime(timezone=True), nullable=True)
    last_outcome_count = Column(Integer, nullable=True)
