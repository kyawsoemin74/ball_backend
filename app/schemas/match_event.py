from pydantic import BaseModel, ConfigDict
from typing import Optional

class MatchEventBase(BaseModel):
    match_id: int
    time_elapsed: int
    time_extra: Optional[int] = None
    
    team_id: int
    team_name: Optional[str] = None
    
    player_id: Optional[int] = None
    player_name: Optional[str] = None
    
    assist_id: Optional[int] = None
    assist_name: Optional[str] = None
    
    type: str
    detail: Optional[str] = None
    comments: Optional[str] = None

class MatchEventResponse(MatchEventBase):
    id: int
    model_config = ConfigDict(from_attributes=True)