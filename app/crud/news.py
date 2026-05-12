from sqlalchemy.orm import Session
from sqlalchemy import desc
from typing import List

from app.models.news import News
from app.schemas.news import NewsCreate, NewsPagination


def get_news_by_category(db: Session, category: str, pagination: NewsPagination) -> List[News]:
    query = db.query(News).filter(News.category == category).order_by(desc(News.published_at))
    total = query.count()
    news = query.offset(pagination.offset).limit(pagination.limit).all()
    return news, total


def create_news(db: Session, news: NewsCreate) -> News:
    db_news = News(**news.model_dump())
    db.add(db_news)
    db.commit()
    db.refresh(db_news)
    return db_news