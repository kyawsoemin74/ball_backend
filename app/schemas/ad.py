from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime


class AdBase(BaseModel):
    title: str
    image_url: Optional[str] = None
    link_url: Optional[str] = None
    active: bool = True


class AdCreate(AdBase):
    pass


class Ad(AdBase):
    id: int
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class AdsResponse(BaseModel):
    ads: List[Ad]