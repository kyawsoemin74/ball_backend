from sqlalchemy import desc, select, func
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Optional

from app.models.news import News
from app.schemas.news import NewsCreate, NewsPagination


async def get_news_by_category(db: AsyncSession, category: str, pagination: NewsPagination):
    if category == "for_you":
        filter_stmt = News.category.in_(["latest", "transfers"])
    else:
        filter_stmt = News.category == category
        
    # စုစုပေါင်းအရေအတွက်ကို အရင်တွက်မည် (Pagination မလုပ်ခင်)
    count_query = select(func.count()).select_from(News).where(filter_stmt)
    total = (await db.execute(count_query)).scalar_one()

    # သတင်းအချက်အလက်များကို ဆွဲထုတ်မည်
    query = select(News).where(filter_stmt).order_by(desc(News.published_at))
    query = query.offset(pagination.offset).limit(pagination.limit)
    result = await db.execute(query)
    news_items = result.scalars().all()

    return news_items, total


async def create_news(db: AsyncSession, news: NewsCreate) -> News:
    db_news = News(**news.model_dump())
    db.add(db_news)
    await db.commit()
    await db.refresh(db_news)
    return db_news


async def get_news_by_id(db: AsyncSession, news_id: int) -> Optional[News]:
    result = await db.execute(select(News).where(News.id == news_id))
    return result.scalar_one_or_none()