from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class TeamBase(BaseModel):
    team_id: int
    name: str
    country: Optional[str] = None
    logo: Optional[str] = None
    stadium: Optional[str] = None
    founded: Optional[int] = None


class TeamCreate(TeamBase):
    pass


class Team(TeamBase):
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True