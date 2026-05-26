import enum
from sqlalchemy import Column, Integer, String, Text, DateTime, Enum
from sqlalchemy.sql import func

from app.db import Base

class NewsCategory(str, enum.Enum):
    LATEST = "latest"
    TRANSFERS = "transfers"
    TIPS = "tips"


class News(Base):
    __tablename__ = "news"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(255), nullable=False)
    content = Column(Text, nullable=False)
    category = Column(Enum(NewsCategory, native_enum=False), nullable=False, default=NewsCategory.LATEST)
    image_url = Column(String(500), nullable=True)
    published_at = Column(DateTime(timezone=True), server_default=func.now())
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())