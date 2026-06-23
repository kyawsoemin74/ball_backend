from sqlalchemy import Column, DateTime, Index, Integer, String, UniqueConstraint
from sqlalchemy.sql import func
from app.db import Base

class Standings(Base):
    __tablename__ = "standings"
    __table_args__ = (
        Index("ix_standings_league_id_season_position", "league_id", "season", "position"),
        UniqueConstraint("league_id", "season", "team_id", name="uq_standings_league_id_season_team_id"),
    )

    id = Column(Integer, primary_key=True, index=True)
    league_id = Column(Integer, index=True, nullable=False)
    season = Column(String(10), nullable=False)
    team_id = Column(Integer, nullable=False)
    team_name = Column(String(255), nullable=True)
    team_logo = Column(String(1024), nullable=True)
    group_name = Column(String(50), nullable=True)
    form = Column(String(20), nullable=True)
    description = Column(String(255), nullable=True)
    position = Column(Integer, nullable=False)
    points = Column(Integer, nullable=False)
    played = Column(Integer, nullable=False)
    won = Column(Integer, nullable=False)
    drawn = Column(Integer, nullable=False)
    lost = Column(Integer, nullable=False)
    goals_for = Column(Integer, nullable=False)
    goals_against = Column(Integer, nullable=False)
    goal_difference = Column(Integer, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
    DateTime(timezone=True),
    server_default=func.now(),
    onupdate=func.now(),
    nullable=False
    )