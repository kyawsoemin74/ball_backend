from sqlalchemy import Column, Identity, Integer, String, DateTime, Text, ForeignKey, Index, UniqueConstraint
from sqlalchemy.orm import relationship, synonym
from sqlalchemy.sql import func

from app.db import Base


class Match(Base):
    __tablename__ = "matches"
    __table_args__ = (
        Index("ix_matches_league_id_season", "league_id", "season"),
        UniqueConstraint("provider", "provider_fixture_id", name="uq_matches_provider_fixture_id"),
    )

    # Local Match identity is independent from the provider fixture identity.
    local_match_id = Column(Integer, Identity(), primary_key=True)
    match_id = synonym("local_match_id")
    provider = Column(String(50), nullable=False, server_default="api-football")
    provider_fixture_id = Column(Integer, nullable=False, index=True)
    fixture_id = synonym("provider_fixture_id")
    
    # League Info
    league_id = Column(Integer, ForeignKey("leagues.league_id", name="fk_matches_league_id_leagues"), nullable=False, index=True)
    season = Column(Integer, nullable=True)
    league_name = Column(String(255), nullable=True)
    league_logo = Column(String(500), nullable=True)
    
    # Country Info
    country_name = Column(String(255), nullable=True)
    country_logo = Column(String(500), nullable=True)
    
    # Match Time (UTC or Asia/Yangon)
    match_time = Column(DateTime(timezone=True), nullable=False)
    
    # Match Status (NS, 1H, 2H, HT, FT, etc.)
    status = Column(String(10), nullable=False, default="NS")
    
    # Elapsed Time in minutes
    elapsed = Column(Integer, nullable=True, default=0)
    
    # Home Team
    home_team = Column(String(255), nullable=False)
    home_team_id = Column(Integer, ForeignKey("teams.team_id"), nullable=True)
    home_team_logo = Column(String(500), nullable=True)
    
    # Away Team
    away_team = Column(String(255), nullable=False)
    away_team_id = Column(Integer, ForeignKey("teams.team_id"), nullable=True)
    away_team_logo = Column(String(500), nullable=True)
    
    # Scores
    home_score = Column(Integer, nullable=False, default=0)
    away_score = Column(Integer, nullable=False, default=0)
    
    # Venue
    venue_id = Column(Integer, ForeignKey("venues.venue_id", ondelete="RESTRICT"), nullable=True, index=True)
    venue_name = Column(String(255), nullable=True)
    venue_city = Column(String(255), nullable=True)

    # Referee
    referee_id = Column(Integer, ForeignKey("referees.referee_id", ondelete="RESTRICT"), nullable=True, index=True)

    league_obj = relationship("League", foreign_keys=[league_id], back_populates="matches")
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())