from pydantic import BaseModel, ConfigDict
from typing import List, Optional
from datetime import datetime
from app.models.news import NewsCategory


class NewsBase(BaseModel):
    title: str
    content: str
    category: NewsCategory
    image_url: Optional[str] = None


class NewsCreate(NewsBase):
    pass


class News(NewsBase):
    id: int
    published_at: datetime
    created_at: datetime
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class NewsPagination(BaseModel):
    limit: int = 10
    offset: int = 0


class NewsResponse(BaseModel):
    news: List[News]
    total: int
    limit: int
    offset: int


class NewsDetailResponse(BaseModel):
    success: bool = True
    data: News


class ErrorResponse(BaseModel):
    success: bool = False
    message: str