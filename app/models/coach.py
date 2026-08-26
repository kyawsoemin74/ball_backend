from sqlalchemy import Column, DateTime, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.sql import func

from app.db import Base


class Coach(Base):
    """
    Canonical Coach Master.

    Identity:
      - coach_id: local canonical Fover identity
      - provider: source provider name (frozen: "api-football")
      - provider_id: external provider identifier when available

    Important source rule:
      - API-Football /coachs payload exposes stable coach records with a numeric id.
      - canonical identity is local coach_id; source metadata remains provider/provider_id.
    """

    __tablename__ = "coaches"

    coach_id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    provider = Column(String(50), nullable=False, server_default="api-football")
    provider_id = Column(String(100), nullable=True, index=True)
    name = Column(String(255), nullable=False)
    normalized_name = Column(String(255), nullable=False, index=True)
    nationality = Column(String(100), nullable=True)
    photo = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("provider", "normalized_name", name="uq_coaches_provider_normalized_name"),
        UniqueConstraint("provider", "provider_id", name="uq_coaches_provider_provider_id"),
        Index("ix_coaches_provider_id", "provider_id"),
        Index("ix_coaches_name", "name"),
    )
