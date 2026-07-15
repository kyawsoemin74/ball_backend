from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.sql import func

from app.db import Base


class Odds(Base):
    __tablename__ = "odds"

    id = Column(Integer, primary_key=True, index=True)
    fixture_id = Column(Integer, ForeignKey("matches.fixture_id"), nullable=False, index=True)
    bookmaker_name = Column(String(255), nullable=True)
    market_name = Column(String(255), nullable=False, index=True)
    selection = Column(String(255), nullable=False, index=True)
    odd_value = Column(String(50), nullable=False)
    myanmar_odd = Column(String(20), nullable=True)
    last_updated = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint(
            "fixture_id",
            "bookmaker_name",
            "market_name",
            "selection",
            name="uq_odds_fixture_bookmaker_market_selection"
        ),
    )
