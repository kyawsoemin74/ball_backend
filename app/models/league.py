from sqlalchemy import Column, Integer, String, DateTime
from sqlalchemy.sql import func

from app.db import Base


class League(Base):
    __tablename__ = "leagues"

    league_id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False, unique=True)
    country = Column(String(255), nullable=True)
    logo = Column(String(500), nullable=True)
    season = Column(String(10), nullable=True)  # e.g., "2023"
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

