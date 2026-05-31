from sqlalchemy import Column, Integer, ForeignKey, JSON, DateTime
from sqlalchemy.sql import func
from app.db import Base

class MatchLineup(Base):
    __tablename__ = "match_lineups"

    id = Column(Integer, primary_key=True, index=True)
    # fixture_id from API-Sports
    match_id = Column(Integer, ForeignKey("matches.fixture_id"), unique=True, index=True, nullable=False)
    data = Column(JSON, nullable=False)  # Stores the full response from the API
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())