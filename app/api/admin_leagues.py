from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_active_admin
from app.db import get_db
from app.models.league import League
from app.schemas.league import League as LeagueSchema, LeagueVisibilityUpdate
from app.services.allowed_league_service import AllowedLeagueService

router = APIRouter()


class AllowedLeagueCreate(BaseModel):
    league_id: int


@router.get("/allowed-leagues", dependencies=[Depends(get_current_active_admin)])
async def get_allowed_leagues(db: AsyncSession = Depends(get_db)):
    """Return the current allowed league list for admins."""
    return await AllowedLeagueService().list_allowed_leagues(db)


@router.post("/allowed-leagues", dependencies=[Depends(get_current_active_admin)])
async def create_allowed_league(payload: AllowedLeagueCreate, db: AsyncSession = Depends(get_db)):
    """Allow a league to be considered for future synchronization eligibility."""
    try:
        allowed_league = await AllowedLeagueService().add_allowed_league(db, payload.league_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return {"league_id": allowed_league.league_id}


@router.delete("/allowed-leagues/{league_id}", dependencies=[Depends(get_current_active_admin)])
async def delete_allowed_league(league_id: int, db: AsyncSession = Depends(get_db)):
    """Remove a league from the allow-list without touching historical data."""
    try:
        await AllowedLeagueService().remove_allowed_league(db, league_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
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
    result = await db.execute(select(League).where(League.league_id == league_id))
    league = result.scalar_one_or_none()

    if league is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="League not found")

    if payload.display_order is not None:
        league.display_order = payload.display_order

    if payload.is_featured is not None:
        league.is_featured = payload.is_featured

    await db.commit()
    await db.refresh(league)

    return league
