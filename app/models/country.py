from sqlalchemy import Column, DateTime, Integer, String
from sqlalchemy.sql import func

from app.db import Base


class Country(Base):
    __tablename__ = "countries"

    country_id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False, unique=True, index=True)
    code = Column(String(10), nullable=True)
    flag = Column(String(500), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
