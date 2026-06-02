from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from app.cache import cache_get_json, cache_set_json, make_cache_key
from app.core.config import settings
from app.db import get_db
from app.schemas.news import NewsResponse, NewsPagination, NewsDetailResponse, News
from app.crud import news

router = APIRouter()

# 🛠️ DRY (Don't Repeat Yourself) လုပ်ထားတဲ့ Helper Function
async def get_news_by_tab_logic(tab: str, limit: int, offset: int, db: AsyncSession):
    cache_key = make_cache_key("news", tab, limit, offset)
    cached = await cache_get_json(cache_key)
    if cached is not None:
        return cached

    pagination = NewsPagination(limit=limit, offset=offset)
    
    # 🟢 "for_you" Logic ကို ဒီမှာ ထည့်သွင်းထားသည် (CRUD ထဲမှာ check လုပ်ရမယ်)
    news_items, total = await news.get_news_by_category(db, tab, pagination)
    
    response = NewsResponse(news=news_items, total=total, limit=limit, offset=offset)
    payload = response.model_dump()
    await cache_set_json(cache_key, payload, settings.REDIS_TTL_NEWS)
    return payload

@router.get("", response_model=NewsResponse)
async def get_news(
    tab: str = Query("for_you", description="for_you, latest, transfers, tips"),
    limit: int = Query(10, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db)
):
    return await get_news_by_tab_logic(tab, limit, offset, db)

@router.get("/latest", response_model=NewsResponse)
async def get_latest_news(
    limit: int = Query(10, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db)
):
    """Get latest news specifically"""
    return await get_news_by_tab_logic("latest", limit, offset, db)

@router.get("/transfers", response_model=NewsResponse)
async def get_transfer_news(
    limit: int = Query(10, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db)
):
    """Get transfer news specifically"""
    return await get_news_by_tab_logic("transfers", limit, offset, db)

@router.get("/tips", response_model=NewsResponse)
async def get_betting_tips(
    limit: int = Query(10, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db)
):
    """Get football tips specifically"""
    return await get_news_by_tab_logic("tips", limit, offset, db)


@router.get(
    "/{news_id}",
    response_model=NewsDetailResponse,
    responses={
        404: {
            "description": "News not found",
            "content": {
                "application/json": {
                    "example": {"success": False, "message": "News not found"}
                }
            }
        }
    }
)
async def get_news_detail(
    news_id: int,
    db: AsyncSession = Depends(get_db)
):
    """Get a single news article by ID."""
    cache_key = make_cache_key("news", "detail", news_id)
    cached = await cache_get_json(cache_key)
    if cached is not None:
        return cached

    news_item = await news.get_news_by_id(db, news_id)
    if not news_item:
        return JSONResponse(status_code=404, content={"success": False, "message": "News not found"})

    payload = {
        "success": True,
        "data": News.model_validate(news_item).model_dump()
    }
    await cache_set_json(cache_key, payload, settings.REDIS_TTL_NEWS)
    return payload

@router.get("/tips", response_model=NewsResponse)
async def get_betting_tips(
    limit: int = Query(10, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db)
):
    """Get football tips specifically"""
    return await get_news_by_tab_logic("tips", limit, offset, db)