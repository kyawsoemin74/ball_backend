from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_active_admin
from app.db import get_db
from app.models.ad_config import AdConfig
from app.schemas.ad import AdsResponse
from app.schemas.ad_config import AdConfigResponse, AdConfigUpdateRequest
from app.crud import ads
from app.services.admob_service import AdMobService

router = APIRouter()


@router.get("/", response_model=AdsResponse)
async def get_active_ads(db: AsyncSession = Depends(get_db)):
    """Return active ad banners"""
    active_ads = await ads.get_active_ads(db)
    return AdsResponse(ads=active_ads)


@router.get("/config", response_model=AdConfigResponse | None, response_model_by_alias=False)
async def get_ad_config(db: AsyncSession = Depends(get_db)):
    """Return the current AdMob configuration."""
    service = AdMobService()
    config = await service.get_current_config(db)
    if config is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ad config not found")
    return config


@router.put(
    "/config",
    response_model=AdConfigResponse,
    dependencies=[Depends(current_active_admin)],
    response_model_by_alias=False,
)
async def update_ad_config(payload: AdConfigUpdateRequest, db: AsyncSession = Depends(get_db)):
    """Update the current AdMob configuration."""
    service = AdMobService()
    config = AdConfig(
        is_enabled=payload.is_enabled,
        banner_android=payload.banner_android,
        banner_ios=payload.banner_ios,
        interstitial_android=payload.interstitial_android,
        interstitial_ios=payload.interstitial_ios,
        rewarded_android=payload.rewarded_android,
        rewarded_ios=payload.rewarded_ios,
        app_open_android=payload.app_open_android,
        app_open_ios=payload.app_open_ios,
    )
    updated_config = await service.update_current_config(db, config)
    return updated_config