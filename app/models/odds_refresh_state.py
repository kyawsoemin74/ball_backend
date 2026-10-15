from sqlalchemy import Boolean, CheckConstraint, Column, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.sql import func

from app.db import Base


class OddsRefreshState(Base):
    __tablename__ = "odds_refresh_state"
    __table_args__ = (
        CheckConstraint("failure_count >= 0", name="ck_odds_refresh_state_failure_count"),
        CheckConstraint(
            "reconciliation_state IN ('IDLE', 'ATTEMPT_IN_PROGRESS', 'COMMIT_PENDING', "
            "'COMMIT_CONFIRMED_CACHE_PENDING', 'COMMIT_NOT_APPLIED', 'MANUAL_REVIEW', 'RESOLVED')",
            name="ck_odds_refresh_state_reconciliation_state",
        ),
        Index("ix_odds_refresh_state_next_eligible", "next_eligible_at"),
        Index("ix_odds_refresh_state_cache_pending", "cache_invalidation_pending"),
        Index("ix_odds_refresh_state_reconciliation", "reconciliation_state"),
    )

    match_id = Column(
        Integer,
        ForeignKey("matches.local_match_id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    )
    failure_count = Column(Integer, nullable=False, server_default="0")
    next_eligible_at = Column(DateTime(timezone=True), nullable=True)
    last_failure_classification = Column(String(64), nullable=True)
    last_attempted_at = Column(DateTime(timezone=True), nullable=True)
    cache_invalidation_pending = Column(Boolean, nullable=False, server_default="false")
    attempt_id = Column(String(36), nullable=True)
    provider_fixture_id = Column(Integer, nullable=True)
    reconciliation_state = Column(String(32), nullable=False, server_default="IDLE")
    pre_attempt_odds_fingerprint = Column(String(64), nullable=True)
    pre_attempt_analytics_fingerprint = Column(String(64), nullable=True)
    candidate_fingerprint = Column(String(64), nullable=True)
    last_reconciliation_at = Column(DateTime(timezone=True), nullable=True)
    last_reconciliation_error = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class OddsProviderCooldown(Base):
    __tablename__ = "odds_provider_cooldown"

    provider_key = Column(String(100), primary_key=True, nullable=False)
    cooldown_until = Column(DateTime(timezone=True), nullable=True)
    last_http_status = Column(Integer, nullable=True)
    last_failure_classification = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
