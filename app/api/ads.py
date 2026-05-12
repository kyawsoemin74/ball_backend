from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.schemas.ad import AdsResponse
from app.crud import ads

router = APIRouter()

@router.get("/", response_model=AdsResponse)
def get_active_ads(db: Session = Depends(get_db)):
    """Return active ad banners"""
    active_ads = ads.get_active_ads(db)
    return AdsResponse(ads=active_ads)