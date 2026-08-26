from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db import Base


class LeagueSeason(Base):
    __tablename__ = "league_seasons"
    __table_args__ = (
        UniqueConstraint("league_id", "season", name="uq_league_seasons_league_id_season"),
    )

    id = Column(Integer, primary_key=True, index=True)
    league_id = Column(Integer, ForeignKey("leagues.league_id", name="fk_league_seasons_league_id"), nullable=False, index=True)
    season = Column(String(20), nullable=False, index=True)
    provider = Column(String(50), nullable=True)
    provider_id = Column(String(100), nullable=True)
    start_date = Column(DateTime(timezone=True), nullable=True)
    end_date = Column(DateTime(timezone=True), nullable=True)
    current = Column(Boolean, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    league = relationship("League", backref="seasons")
