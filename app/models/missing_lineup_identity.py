from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.sql import func

from app.db import Base


class MissingLineupIdentity(Base):
    __tablename__ = "missing_lineup_identities"
    __table_args__ = (
        UniqueConstraint(
            "match_id",
            "provider_team_id",
            "roster_role",
            "lineup_position",
            name="uq_missing_lineup_identity_entry",
        ),
    )

    missing_identity_id = Column(Integer, primary_key=True, autoincrement=True)
    match_id = Column(Integer, ForeignKey("matches.local_match_id"), nullable=False, index=True)
    provider_fixture_id = Column(String(100), nullable=True)
    local_team_id = Column(Integer, ForeignKey("teams.team_id"), nullable=True)
    provider_team_id = Column(String(100), nullable=False)
    provider = Column(String(50), nullable=False, server_default="api-football")
    player_name = Column(String(255), nullable=True)
    shirt_number = Column(Integer, nullable=True)
    position = Column(String(50), nullable=True)
    grid = Column(String(50), nullable=True)
    roster_role = Column(String(20), nullable=False)
    lineup_position = Column(Integer, nullable=False)
    missing_reason = Column(String(100), nullable=False)
    resolution_status = Column(String(30), nullable=False, server_default="MISSING")
    retry_eligible = Column(Integer, nullable=False, server_default="1")
    observation_count = Column(Integer, nullable=False, server_default="1")
    first_seen = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    last_seen = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    resolved_local_player_id = Column(Integer, ForeignKey("players.player_id"), nullable=True)
    raw_identity_state = Column(Text, nullable=True)
