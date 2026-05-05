from sqlalchemy import Column, Integer, String

from app.db.database import Base


class League(Base):
    __tablename__ = "leagues"

    league_id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False, unique=True)
    country = Column(String(255), nullable=True)
    logo = Column(String(500), nullable=True)
