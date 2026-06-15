from sqlalchemy import Column, DateTime, Integer
from sqlalchemy.sql import func

from app.db import Base


class AllowedLeague(Base):
    __tablename__ = "allowed_leagues"

    league_id = Column(Integer, primary_key=True, index=True, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
