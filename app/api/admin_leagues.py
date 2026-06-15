from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_active_admin
from app.db import get_db
from app.models.league import League
from app.schemas.league import League as LeagueSchema, LeagueVisibilityUpdate

router = APIRouter()


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
