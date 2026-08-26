from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.sql import func

from app.db import Base


class Team(Base):
    __tablename__ = "teams"
    __table_args__ = (
        UniqueConstraint("provider", "provider_id", name="uq_teams_provider_provider_id"),
        UniqueConstraint("coach_id", name="uq_teams_coach_id"),
    )

    team_id = Column(Integer, primary_key=True, index=True)
    provider = Column(String(50), nullable=False, server_default="api-football")
    provider_id = Column(String(100), nullable=True, index=True)
    coach_id = Column(Integer, ForeignKey("coaches.coach_id", name="fk_teams_coach_id", ondelete="RESTRICT"), nullable=True)
    country_id = Column(Integer, ForeignKey("countries.country_id", name="fk_teams_country_id"), nullable=True, index=True)
    name = Column(String(255), nullable=False)
    country = Column(String(255), nullable=True)
    logo = Column(String(500), nullable=True)
    stadium = Column(String(255), nullable=True)
    founded = Column(Integer, nullable=True)  # year
    current_league_id = Column(Integer, nullable=True)
    current_season = Column(String(10), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

