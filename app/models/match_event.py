from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Text
from sqlalchemy.sql import func
from app.db import Base

class MatchEvent(Base):
    __tablename__ = "match_events"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    # Links to the fixture_id column in the matches table
    match_id = Column(Integer, ForeignKey("matches.fixture_id"), index=True, nullable=False)
    
    time_elapsed = Column(Integer, nullable=False)
    time_extra = Column(Integer, nullable=True)
    
    team_id = Column(Integer, nullable=False)
    team_name = Column(String(255), nullable=True)
    
    player_id = Column(Integer, nullable=True)
    player_name = Column(String(255), nullable=True)
    
    assist_id = Column(Integer, nullable=True)
    assist_name = Column(String(255), nullable=True)
    
    type = Column(String(50), nullable=False)  # e.g., Goal, Card, subst, var
    detail = Column(String(255), nullable=True)
    comments = Column(Text, nullable=True)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)