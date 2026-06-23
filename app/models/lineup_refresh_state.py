from sqlalchemy import Column, DateTime, ForeignKey, Integer
from sqlalchemy.sql import func

from app.db import Base


class LineupRefreshState(Base):
    __tablename__ = "lineup_refresh_state"

    match_id = Column(Integer, ForeignKey("matches.fixture_id"), primary_key=True, nullable=False, index=True)
    last_refreshed_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
