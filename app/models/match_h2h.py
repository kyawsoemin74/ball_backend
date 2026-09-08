from sqlalchemy import Column, Integer, String, DateTime, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import func
from app.db import Base

class MatchH2H(Base):
    __tablename__ = "match_h2h"

    id = Column(Integer, primary_key=True)
    # Format: "min_team_id-max_team_id"
    h2h_key = Column(String(50), index=True, nullable=False)
    data = Column(JSONB, nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (UniqueConstraint("h2h_key", name="match_h2h_h2h_key_key"),)