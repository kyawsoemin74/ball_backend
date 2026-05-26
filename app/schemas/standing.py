from pydantic import BaseModel, ConfigDict
from datetime import datetime
from typing import Optional

class StandingBase(BaseModel):
    league_id: int
    season: int
    rank: int
    team_id: int
    team_name: str
    team_logo: Optional[str] = None
    points: int
    goals_diff: int
    played: int
    win: int
    draw: int
    lose: int

class StandingCreate(StandingBase):
    pass

class StandingResponse(StandingBase):
    id: int
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)