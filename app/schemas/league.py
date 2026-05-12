from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class LeagueBase(BaseModel):
    league_id: int
    name: str
    country: Optional[str] = None
    logo: Optional[str] = None
    season: Optional[str] = None


class LeagueCreate(LeagueBase):
    pass


class League(LeagueBase):
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True