from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, text
from sqlalchemy.sql import func

from app.db import Base


class PlayerTeamMembership(Base):
    __tablename__ = "player_team_memberships"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "player_id",
            "team_id",
            "valid_from",
            name="uq_player_team_membership_identity",
        ),
        Index(
            "uq_player_team_membership_current_identity",
            "provider",
            "player_id",
            "team_id",
            unique=True,
            postgresql_where=text("valid_from IS NULL"),
        ),
        Index("ix_player_team_memberships_player_current", "player_id", "is_current"),
        Index("ix_player_team_memberships_team", "team_id"),
        Index("ix_player_team_memberships_provider_team", "provider", "provider_team_id"),
    )

    player_team_membership_id = Column(Integer, primary_key=True, autoincrement=True)
    player_id = Column(Integer, ForeignKey("players.player_id"), nullable=False)
    team_id = Column(Integer, ForeignKey("teams.team_id"), nullable=False)
    provider = Column(String(50), nullable=False, server_default="api-football")
    provider_team_id = Column(String(100), nullable=False)
    valid_from = Column(DateTime(timezone=True), nullable=True)
    valid_to = Column(DateTime(timezone=True), nullable=True)
    is_current = Column(Boolean, nullable=False, server_default="false")
    source = Column(String(50), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)