from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime


class LeagueBase(BaseModel):
    league_id: int
    name: str
    country: Optional[str] = None
    country_code: Optional[str] = None
    logo: Optional[str] = None
    season: Optional[str] = None
    is_featured: bool = False
    display_order: int = 999


class LeagueCreate(LeagueBase):
    pass


class League(LeagueBase):
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class LeagueGroupResponse(BaseModel):
    type: str
    title: Optional[str] = None
    country: Optional[str] = None
    leagues: List[LeagueBase]


class TopScorerItem(BaseModel):
    player_id: Optional[int] = None
    player_name: Optional[str] = None
    team_id: Optional[int] = None
    team_name: Optional[str] = None
    goals: Optional[int] = None
    assists: Optional[int] = None
    appearances: Optional[int] = None
    photo: Optional[str] = None


class TopScorersResponse(BaseModel):
    league_id: int
    season: int
    players: List[TopScorerItem] = []
