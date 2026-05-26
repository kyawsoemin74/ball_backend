from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
import logging
from typing import List, Dict, Any

from app.api.deps import current_active_admin
from app.cache import cache_get_json, cache_set_json, make_cache_key
from app.core.config import settings
from app.db import get_db
from app.models.league import League
from app.models.standing import Standings
from app.schemas.league import League as LeagueSchema
from app.schemas.standing import StandingResponse
from app.services.football import football_service

router = APIRouter()
logger = logging.getLogger(__name__)

@router.get("/{league_id}", response_model=LeagueSchema)
async def get_league_details(league_id: int, db: AsyncSession = Depends(get_db)):
    """Get league details, caching in DB and Redis"""
    cache_key = make_cache_key("league", league_id)
    cached = await cache_get_json(cache_key)
    if cached is not None:
        return cached

    # Check DB first
    result = await db.execute(select(League).where(League.league_id == league_id))
    league = result.scalar_one_or_none()
    if league:
        logger.info(f"League {league_id} fetched from DB")
        payload = LeagueSchema.from_orm(league).dict()
        await cache_set_json(cache_key, payload, settings.REDIS_TTL_LEAGUE_TEAM)
        return payload

    # Fetch from API
    result = await football_service.get_league_details(league_id)
    if not result or "response" not in result or not result["response"]:
        raise HTTPException(status_code=404, detail="League not found")

    league_data = result["response"][0]
    upserted_league = await football_service.upsert_league(db, league_data)
    logger.info(f"League {league_id} fetched from API and cached")
    payload = LeagueSchema.from_orm(upserted_league).dict()
    await cache_set_json(cache_key, payload, settings.REDIS_TTL_LEAGUE_TEAM)
    return payload

@router.get("/{league_id}/standing", response_model=List[StandingResponse])
async def get_league_standings(league_id: int, season: str = "2023", db: AsyncSession = Depends(get_db)):
    """Get league standings, ensuring fresh data"""
    cache_key = make_cache_key("standings", league_id, season)
    cached = await cache_get_json(cache_key)
    if cached is not None:
        return cached

    # Check if we have recent standings
    existing_result = await db.execute(
        select(Standings).where(
            Standings.league_id == league_id,
            Standings.season == int(season)
        )
    )
    existing = existing_result.scalar_one_or_none()

    if not existing:
        # Fetch from API
        result = await football_service.get_league_standings(league_id, int(season))
        if not result or "response" not in result or not result["response"]:
            raise HTTPException(status_code=404, detail="Standings not found")

        standings_data = result["response"][0]["league"]["standings"][0]  # Assuming single group
        await football_service.upsert_standings(db, standings_data, league_id, season)
        logger.info(f"Standings for league {league_id} season {season} fetched from API and cached")

    standings_result = await db.execute(
        select(Standings)
        .where(
            Standings.league_id == league_id,
            Standings.season == int(season)
        )
        .order_by(Standings.rank)
    )
    standings = standings_result.scalars().all()
    
    payload = [StandingResponse.model_validate(s).model_dump(mode="json") for s in standings]
    await cache_set_json(cache_key, payload, settings.REDIS_TTL_STANDINGS)
    return standings

@router.post("/sync/standings/{league_id}", status_code=status.HTTP_200_OK, dependencies=[Depends(current_active_admin)])
async def sync_league_standings(
    league_id: int,
    season: int = Query(2023, description="The season year"),
    db: AsyncSession = Depends(get_db)
) -> Dict[str, Any]:
    """Explicitly sync standings for a league and season from API-Sports"""
    return await football_service.sync_standings(db=db, league_id=league_id, season=season)