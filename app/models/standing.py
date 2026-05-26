from sqlalchemy import Column, Integer, String, DateTime
from sqlalchemy.sql import func
from app.db import Base

class Standings(Base):
    __tablename__ = "standings"

    id = Column(Integer, primary_key=True, index=True)
    league_id = Column(Integer, index=True, nullable=False)
    season = Column(Integer, nullable=False)
    rank = Column(Integer, nullable=False)
    team_id = Column(Integer, nullable=False)
    team_name = Column(String, nullable=False)
    team_logo = Column(String, nullable=True)
    points = Column(Integer, default=0)
    goals_diff = Column(Integer, default=0)
    played = Column(Integer, default=0)
    win = Column(Integer, default=0)
    draw = Column(Integer, default=0)
    lose = Column(Integer, default=0)
    updated_at = Column(
        DateTime(timezone=True), 
        server_default=func.now(), 
        onupdate=func.now()
    )