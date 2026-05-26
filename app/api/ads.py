from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.schemas.ad import AdsResponse
from app.crud import ads

router = APIRouter()

@router.get("/", response_model=AdsResponse)
async def get_active_ads(db: AsyncSession = Depends(get_db)):
    """Return active ad banners"""
    active_ads = await ads.get_active_ads(db)
    return AdsResponse(ads=active_ads)