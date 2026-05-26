from sqlalchemy import Column, Integer, String, JSON, DateTime
from sqlalchemy.sql import func
from app.db import Base

class MatchH2H(Base):
    __tablename__ = "match_h2h"

    id = Column(Integer, primary_key=True, index=True)
    # Format: "min_team_id-max_team_id"
    h2h_key = Column(String(50), unique=True, index=True, nullable=False)
    data = Column(JSON, nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())