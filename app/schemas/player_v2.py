from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict


class PlayerV2Response(BaseModel):
    player_id: int
    provider: str
    provider_player_id: str
    name: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    nationality: Optional[str] = None
    position: Optional[str] = None
    photo: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class SquadPlayerV2(BaseModel):
    player_id: Optional[int] = None
    provider_player_id: str
    player_name: Optional[str] = None
    age: Optional[int] = None
    nationality: Optional[str] = None
    position: Optional[str] = None
    photo: Optional[str] = None


class SquadResponseV2(BaseModel):
    team_id: int
    team_name: Optional[str] = None
    players: list[SquadPlayerV2] = []


class TopScorerV2(BaseModel):
    player_id: Optional[int] = None
    provider_player_id: str
    player_name: Optional[str] = None
    team_id: Optional[int] = None
    team_name: Optional[str] = None
    goals: Optional[int] = None
    assists: Optional[int] = None
    appearances: Optional[int] = None
    photo: Optional[str] = None


class TopScorersResponseV2(BaseModel):
    league_id: int
    season: int
    players: list[TopScorerV2] = []


class EventResponseV2(BaseModel):
    id: Optional[int] = None
    match_id: int
    time_elapsed: int
    time_extra: Optional[int] = None
    team_id: int
    team_name: Optional[str] = None
    player_id: Optional[int] = None
    provider_player_id: Optional[str] = None
    player_name: Optional[str] = None
    assist_id: Optional[int] = None
    provider_assist_id: Optional[str] = None
    assist_name: Optional[str] = None
    type: str
    detail: Optional[str] = None
    comments: Optional[str] = None


class LineupResponseV2(BaseModel):
    model_config = ConfigDict(extra="allow")

    data: Any