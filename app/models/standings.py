from sqlalchemy import Column, Integer, String, ForeignKey, DateTime
from sqlalchemy.sql import func

from app.db import Base


class Standings(Base):
    __tablename__ = "standings"

    id = Column(Integer, primary_key=True, index=True)
    league_id = Column(Integer, ForeignKey("leagues.league_id"), nullable=False)
    season = Column(String(10), nullable=False)  # e.g., "2023"
    team_id = Column(Integer, ForeignKey("teams.team_id"), nullable=False)
    position = Column(Integer, nullable=False)
    points = Column(Integer, nullable=False)
    played = Column(Integer, nullable=False)
    won = Column(Integer, nullable=False)
    drawn = Column(Integer, nullable=False)
    lost = Column(Integer, nullable=False)
    goals_for = Column(Integer, nullable=False)
    goals_against = Column(Integer, nullable=False)
    goal_difference = Column(Integer, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())