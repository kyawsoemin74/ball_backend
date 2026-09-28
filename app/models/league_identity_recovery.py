from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.sql import func

from app.db import Base


class LeagueIdentityRecovery(Base):
    __tablename__ = "league_identity_recovery"
    __table_args__ = (
        UniqueConstraint("league_id", name="uq_league_identity_recovery_league_id"),
        Index("ix_league_identity_recovery_retry", "state", "next_retry_at"),
    )

    recovery_id = Column(Integer, primary_key=True, autoincrement=True)
    league_id = Column(
        Integer,
        ForeignKey("leagues.league_id", ondelete="CASCADE"),
        nullable=False,
        index=False,
    )
    state = Column(String(40), nullable=False, server_default="IDENTITY_RESOLUTION_REQUIRED")
    attempt_count = Column(Integer, nullable=False, server_default="0")
    next_retry_at = Column(DateTime(timezone=True), nullable=True)
    last_attempted_at = Column(DateTime(timezone=True), nullable=True)
    last_error_code = Column(String(100), nullable=True)
    last_error_message = Column(Text, nullable=True)
    provider = Column(String(50), nullable=True)
    resolved_provider_id = Column(String(100), nullable=True)
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
