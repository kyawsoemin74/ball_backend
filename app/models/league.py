from sqlalchemy import Boolean, Column, Integer, String, DateTime, ForeignKey, UniqueConstraint, Index
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db import Base


class League(Base):
    __tablename__ = "leagues"
    __table_args__ = (
        UniqueConstraint("provider", "provider_id", name="uq_leagues_provider_provider_id"),
        Index("ix_leagues_provider_id", "provider_id"),
        Index("ix_leagues_is_featured", "is_featured"),
        Index("ix_leagues_display_order", "display_order"),
    )

    # League Master Identity
    league_id = Column(Integer, primary_key=True)
    provider = Column(String(50), nullable=False, server_default='api-football')
    provider_id = Column(String(100), nullable=True, index=True)
    name = Column(String(255), nullable=False)
    logo = Column(String(500), nullable=True)
    type = Column(String(50), nullable=True)  # e.g., "domestic", "club", "international"
    national = Column(Boolean, nullable=True)  # is_national_team_league
    country_id = Column(Integer, ForeignKey("countries.country_id", name="fk_leagues_country_id"), nullable=True, index=True)
    
    # Legacy fields (kept for backward compatibility, to be deprecated)
    country = Column(String(255), nullable=True)
    country_code = Column(String(20), nullable=True)
    season = Column(String(10), nullable=True)  # will move to league_seasons in Step 3
    is_featured = Column(Boolean, nullable=False, server_default='false')
    display_order = Column(Integer, nullable=False, server_default='999')
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    matches = relationship("Match", back_populates="league_obj")


