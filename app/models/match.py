from sqlalchemy import Column, Integer, String, DateTime, Text
from sqlalchemy.sql import func

from app.db.database import Base


class Match(Base):
    __tablename__ = "matches"

    # Primary Key
    fixture_id = Column(Integer, primary_key=True, index=True)
    
    # League Info
    league_id = Column(Integer, nullable=False, index=True)
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
    home_team_logo = Column(String(500), nullable=True)
    
    # Away Team
    away_team = Column(String(255), nullable=False)
    away_team_logo = Column(String(500), nullable=True)
    
    # Scores
    home_score = Column(Integer, nullable=False, default=0)
    away_score = Column(Integer, nullable=False, default=0)
    
    # Venue
    venue_name = Column(String(255), nullable=True)
    venue_city = Column(String(255), nullable=True)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())