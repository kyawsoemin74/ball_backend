from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.schemas.news import NewsResponse, NewsPagination
from app.crud import news

router = APIRouter()

@router.get("/latest", response_model=NewsResponse)
def get_latest_news(
    limit: int = Query(10, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db)
):
    """Return paginated latest news"""
    pagination = NewsPagination(limit=limit, offset=offset)
    news_items, total = news.get_news_by_category(db, "latest", pagination)
    return NewsResponse(news=news_items, total=total, limit=limit, offset=offset)

@router.get("/transfers", response_model=NewsResponse)
def get_transfers_news(
    limit: int = Query(10, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db)
):
    """Return paginated transfers news"""
    pagination = NewsPagination(limit=limit, offset=offset)
    news_items, total = news.get_news_by_category(db, "transfers", pagination)
    return NewsResponse(news=news_items, total=total, limit=limit, offset=offset)

@router.get("/tips", response_model=NewsResponse)
def get_tips_news(
    limit: int = Query(10, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db)
):
    """Return paginated tips news"""
    pagination = NewsPagination(limit=limit, offset=offset)
    news_items, total = news.get_news_by_category(db, "tips", pagination)
    return NewsResponse(news=news_items, total=total, limit=limit, offset=offset)