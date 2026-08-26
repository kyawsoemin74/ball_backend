from sqlalchemy import Column, String, Integer, Text, DateTime, UniqueConstraint, Index
from sqlalchemy.sql import func

from app.db import Base


class Venue(Base):
    """
    Canonical Venue Master.
    
    Represents a single physical venue/stadium.
    
    Identity:
      - venue_id: Local Fover identity (PK, auto-increment)
      - provider: External provider name (frozen: "api-football")
      - provider_id: External provider venue ID (numeric from API-Football)
    
    Uniqueness:
      - UNIQUE(provider, provider_id) ensures ONE canonical Venue per provider venue
    
    Profile:
      - name, city, country, country_code: Venue location
      - capacity: Stadium capacity (optional)
      - surface: Playing surface type (optional)
      - image: Venue image URL (optional)
    
    Relationships (future, not in this step):
      - Matches may have venue_id → venues.venue_id
      - Teams may have home_venue_id → venues.venue_id
    
    Architecture:
      - Venue is SEPARATE from Match and Team
      - Legacy match.venue_name and match.venue_city preserved
      - Legacy team.stadium preserved
      - No FK constraints added in this step
    """

    __tablename__ = "venues"

    # Identity
    venue_id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    provider = Column(String(50), nullable=False, server_default="api-football")
    provider_id = Column(String(100), nullable=False, index=True)

    # Profile
    name = Column(String(255), nullable=False)
    city = Column(String(255), nullable=True)
    country = Column(String(100), nullable=True)
    country_code = Column(String(2), nullable=True)
    capacity = Column(Integer, nullable=True)
    surface = Column(String(50), nullable=True)
    image = Column(Text, nullable=True)

    # Timestamps
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("provider", "provider_id", name="uq_venues_provider_provider_id"),
        Index("ix_venues_name", "name"),
        Index("ix_venues_provider_id", "provider_id"),
    )

    def __repr__(self) -> str:
        return f"<Venue(venue_id={self.venue_id}, provider_id={self.provider_id}, name={self.name}, city={self.city})>"
