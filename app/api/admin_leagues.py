import logging

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache import make_cache_key
from app.core.security import get_current_active_admin
from app.db import get_db
from app.models.league import League
from app.schemas.league import (
    League as LeagueSchema,
    LeagueRegistrationRequest,
    LeagueRegistrationResponse,
    LeagueVisibilityUpdate,
)
from app.services.allowed_league_service import AllowedLeagueService
from app.services.cache_service import CacheService
from app.services.football import football_service

router = APIRouter()
logger = logging.getLogger(__name__)


class AllowedLeagueCreate(BaseModel):
    league_id: int


@router.post(
    "/leagues",
    response_model=LeagueRegistrationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def register_league(
    payload: LeagueRegistrationRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
    admin=Depends(get_current_active_admin),
):
    """Register a provider League without authorizing synchronization."""
    service = football_service.league_service
    try:
        league, created = await service.register_league(
            db,
            payload.provider,
            payload.provider_id,
        )
        if created:
            await db.commit()
            try:
                await service.cache_service.delete(
                    make_cache_key("league", league.league_id)
                )
                await service.cache_service.delete(make_cache_key("leagues_grouped"))
            except Exception:
                logger.exception(
                    "League registration cache invalidation failed",
                    extra={"league_id": league.league_id},
                )
            logger.info(
                "League registered",
                extra={
                    "admin_id": getattr(admin, "id", None),
                    "admin_username": getattr(admin, "username", None),
                    "provider": league.provider,
                    "provider_id": league.provider_id,
                    "league_id": league.league_id,
                    "result": "created",
                },
            )
        else:
            response.status_code = status.HTTP_200_OK
        return league
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except LookupError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except ConnectionError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="League identity or name already exists",
        ) from exc
    except Exception as exc:
        await db.rollback()
        logger.exception("League registration failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="League registration failed",
        ) from exc


@router.get("/allowed-leagues", dependencies=[Depends(get_current_active_admin)])
async def get_allowed_leagues(db: AsyncSession = Depends(get_db)):
    """Return the current allowed league list for admins."""
    return await AllowedLeagueService().list_allowed_leagues(db)


@router.post(
    "/allowed-leagues",
    dependencies=[Depends(get_current_active_admin)],
)
async def create_allowed_league(
    payload: AllowedLeagueCreate,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    """Allow a league to be considered for future synchronization eligibility."""
    try:
        allowed_league, created = await AllowedLeagueService().add_allowed_league(
            db,
            payload.league_id,
        )
        await db.commit()
        if created:
            await CacheService().delete(make_cache_key("leagues_grouped"))
        else:
            response.status_code = status.HTTP_200_OK
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="League is already allowed",
        ) from exc
    return {"league_id": allowed_league.league_id}


@router.delete(
    "/allowed-leagues/{league_id}",
    dependencies=[Depends(get_current_active_admin)],
)
async def delete_allowed_league(league_id: int, db: AsyncSession = Depends(get_db)):
    """Remove a league from the allow-list without touching historical data."""
    try:
        await AllowedLeagueService().remove_allowed_league(db, league_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    return {"message": "Allowed league removed", "league_id": league_id}


@router.patch(
    "/leagues/{league_id}",
    response_model=LeagueSchema,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(get_current_active_admin)],
)
async def patch_admin_league(
    league_id: int,
    payload: LeagueVisibilityUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Update a league's visibility settings for admin users."""
    result = await db.execute(
        select(League).where(League.league_id == league_id)
    )
    league = result.scalar_one_or_none()

    if league is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="League not found",
        )

    if payload.display_order is not None:
        league.display_order = payload.display_order

    if payload.is_featured is not None:
        league.is_featured = payload.is_featured

    try:
        await db.commit()
    except Exception:
        await db.rollback()
        raise

    await db.refresh(league)
    try:
        cache_service = CacheService()
        await cache_service.delete(make_cache_key("league", league_id))
        await cache_service.delete(make_cache_key("leagues_grouped"))
    except Exception:
        logger.exception(
            "League metadata cache invalidation failed",
            extra={"league_id": league_id},
        )

    return league
