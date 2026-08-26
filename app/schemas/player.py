from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict


class PlayerBase(BaseModel):
    provider: str = "api-football"
    provider_id: str
    name: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    nationality: Optional[str] = None
    birth_date: Optional[datetime] = None
    birth_place: Optional[str] = None
    birth_country: Optional[str] = None
    height: Optional[int] = None
    weight: Optional[int] = None
    position: Optional[str] = None
    preferred_foot: Optional[str] = None
    photo: Optional[str] = None


class PlayerCreate(PlayerBase):
    pass


class PlayerUpdate(BaseModel):
    name: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    nationality: Optional[str] = None
    birth_date: Optional[datetime] = None
    birth_place: Optional[str] = None
    birth_country: Optional[str] = None
    height: Optional[int] = None
    weight: Optional[int] = None
    position: Optional[str] = None
    preferred_foot: Optional[str] = None
    photo: Optional[str] = None


class Player(PlayerBase):
    player_id: int
    created_at: datetime
    updated_at: datetime
    
    model_config = ConfigDict(from_attributes=True)


class PlayerResponse(Player):
    pass
