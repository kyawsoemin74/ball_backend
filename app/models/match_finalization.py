from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.sql import func

from app.db import Base


class MatchFinalization(Base):
    __tablename__ = "match_finalization"
    __table_args__ = (
        CheckConstraint(
            "state IN ('PENDING', 'RUNNING', 'SUCCESS', 'FAILED', 'EXHAUSTED')",
            name="ck_match_finalization_state",
        ),
        CheckConstraint(
            "attempt_count >= 0",
            name="ck_match_finalization_attempt_count",
        ),
        CheckConstraint(
            "(state = 'SUCCESS' AND completed_at IS NOT NULL) OR "
            "(state != 'SUCCESS' AND completed_at IS NULL)",
            name="ck_match_finalization_completed",
        ),
        Index("ix_match_finalization_retry", "state", "next_retry_at"),
    )

    match_id = Column(
        Integer,
        ForeignKey("matches.local_match_id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    )
    state = Column(String(20), nullable=False, server_default="PENDING")
    attempt_count = Column(Integer, nullable=False, server_default="0")
    next_retry_at = Column(DateTime(timezone=True), nullable=True)
    last_attempted_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    last_error_category = Column(String(50), nullable=True)
    last_error_message = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
