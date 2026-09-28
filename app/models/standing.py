from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.db import Base
from app.models.league_season import LeagueSeason

class Standings(Base):
    __tablename__ = "standings"
    __table_args__ = (
        Index("ix_standings_league_season_position", "league_season_id", "position"),
        UniqueConstraint("league_season_id", "team_id", name="uq_standings_league_season_team_id"),
    )

    id = Column(Integer, primary_key=True)
    league_season_id = Column(
        Integer,
        ForeignKey("league_seasons.id", name="fk_standings_league_season_id_league_seasons"),
        nullable=False,
    )
    team_id = Column(Integer, ForeignKey("teams.team_id", name="fk_standings_team_id_teams"), nullable=False)
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

    league_season = relationship(LeagueSeason, lazy="joined")

    @property
    def league_id(self) -> int:
        return self.league_season.league_id

    @property
    def season(self) -> str:
        return self.league_season.season