from sqlalchemy import Column, Integer, ForeignKey, JSON, DateTime
from sqlalchemy.orm import synonym
from sqlalchemy.sql import func
from app.db import Base

class MatchLineup(Base):
    __tablename__ = "match_lineups"

    id = Column(Integer, primary_key=True)
    # Canonical local Match identity. Provider fixture IDs remain Match metadata.
    local_match_id = Column(Integer, ForeignKey("matches.local_match_id"), unique=True, index=True, nullable=False)
    match_id = synonym("local_match_id")
    data = Column(JSON, nullable=False)  # Stores the full response from the API
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())