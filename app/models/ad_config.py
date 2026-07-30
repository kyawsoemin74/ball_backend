from sqlalchemy import Boolean, Column, DateTime, Integer, String
from sqlalchemy.sql import func

from app.db import Base


class AdConfig(Base):
    __tablename__ = "ad_configs"

    id = Column(Integer, primary_key=True, index=True)
    is_enabled = Column(Boolean, nullable=False, server_default="false")
    banner_android = Column(String(255), nullable=True)
    banner_ios = Column(String(255), nullable=True)
    interstitial_android = Column(String(255), nullable=True)
    interstitial_ios = Column(String(255), nullable=True)
    rewarded_android = Column(String(255), nullable=True)
    rewarded_ios = Column(String(255), nullable=True)
    app_open_android = Column(String(255), nullable=True)
    app_open_ios = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
