from sqlalchemy import Column, Integer, String

from app.db.database import Base


class Team(Base):
    __tablename__ = "teams"

    team_id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False, unique=True)
    country = Column(String(255), nullable=True)
    logo = Column(String(500), nullable=True)
