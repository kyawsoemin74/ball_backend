from pydantic import BaseModel
from typing import List
from datetime import datetime


class StandingBase(BaseModel):
    league_id: int
    season: str
    team_id: int
    position: int
    points: int
    played: int
    won: int
    drawn: int
    lost: int
    goals_for: int
    goals_against: int
    goal_difference: int


class StandingCreate(StandingBase):
    pass


class Standing(StandingBase):
    id: int
    created_at: datetime
    updated_at: Optional[datetime] = None


class StandingResponse(StandingBase):
    pass


class StandingsResponse(BaseModel):
    league_id: int
    season: str
    standings: List[StandingResponse]