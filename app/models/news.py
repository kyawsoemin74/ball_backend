import enum
from sqlalchemy import Column, Integer, String, Text, DateTime
from sqlalchemy.sql import func

from app.db import Base

class NewsCategory(str, enum.Enum):
    LATEST = "latest"
    TRANSFERS = "transfers"
    TIPS = "tips"

    @classmethod
    def normalize(cls, value: str) -> str:
        if isinstance(value, cls):
            return value.value

        if isinstance(value, str):
            try:
                return cls(value).value
            except ValueError:
                pass

        choices = ", ".join(category.value for category in cls)
        raise ValueError(f"Invalid news category {value!r}. Expected one of: {choices}.")


class News(Base):
    __tablename__ = "news"

    id = Column(Integer, primary_key=True)
    title = Column(String(255), nullable=False)
    content = Column(Text, nullable=False)
    category = Column(String(50), nullable=False, default=NewsCategory.LATEST.value)
    image_url = Column(String(500), nullable=True)
    published_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())