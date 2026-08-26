from sqlalchemy import Column, Integer, String, DateTime, Text, Float, UniqueConstraint, Index
from sqlalchemy.sql import func
from app.db import Base


class Player(Base):
    """
    Player Master - Canonical Player Identity.
    
    Canonical identity: (provider, provider_id)
    
    This is the single source of truth for player identity.
    All domains (Events, Lineups, Squad, Statistics, etc.) reference this table.
    
    Temporal data (Market Value, Transfers, Injuries, Statistics, Team Membership)
    belongs to separate domain tables, NOT here.
    """
    
    __tablename__ = "players"
    
    # Canonical Identity
    player_id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    provider = Column(String(50), nullable=False, server_default="api-football")
    provider_id = Column(String(100), nullable=False, index=True)
    
    # Profile - Relatively Stable
    first_name = Column(String(100), nullable=True)
    last_name = Column(String(100), nullable=True)
    name = Column(String(255), nullable=False)  # Full name from provider
    
    # Location / Background
    nationality = Column(String(100), nullable=True)
    birth_date = Column(DateTime, nullable=True)
    birth_place = Column(String(255), nullable=True)
    birth_country = Column(String(100), nullable=True)
    
    # Physical Attributes
    height = Column(Integer, nullable=True)  # in cm
    weight = Column(Integer, nullable=True)  # in kg
    
    # Playing Style
    position = Column(String(50), nullable=True)  # e.g., "Goalkeeper", "Defender"
    preferred_foot = Column(String(20), nullable=True)  # "left", "right"
    
    # Media
    photo = Column(Text, nullable=True)  # Photo URL
    
    # Temporal
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    
    # Constraints
    __table_args__ = (
        UniqueConstraint("provider", "provider_id", name="uq_players_provider_provider_id"),
        Index("ix_players_provider_id", "provider_id"),
        Index("ix_players_name", "name"),
    )
