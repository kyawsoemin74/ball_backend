from pydantic import BaseModel, ConfigDict
from datetime import datetime
from typing import Optional

class StandingBase(BaseModel):
    league_id: int
    season: str
    position: int
    team_id: int
    team_name: str
    team_logo: Optional[str] = None
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

class StandingResponse(StandingBase):
    id: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)