from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime


class NewsBase(BaseModel):
    title: str
    content: str
    category: str


class NewsCreate(NewsBase):
    pass


class News(NewsBase):
    id: int
    published_at: datetime
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class NewsPagination(BaseModel):
    limit: int = 10
    offset: int = 0


class NewsResponse(BaseModel):
    news: List[News]
    total: int
    limit: int
    offset: int