from fastapi import APIRouter, Depends, HTTPException, Path
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
import logging

from app.cache import cache_get_json, cache_set_json, make_cache_key
from app.core.config import settings
from app.db import get_db
from app.models.team import Team
from app.schemas.match import MatchResponse
from app.schemas.standing import StandingResponse
from app.schemas.team import Team as TeamSchema, TeamFixturesResponse, TeamSquadResponse, TeamStatisticsResponse
from app.services.football import football_service

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/{team_id}/fixtures", response_model=TeamFixturesResponse)
async def get_team_fixtures(team_id: int = Path(..., gt=0), db: AsyncSession = Depends(get_db)):
    """Get recent and upcoming fixtures for a team."""
    result = await football_service.get_team_fixtures(db, team_id)
    if not result or "error" in result:
        raise HTTPException(status_code=404, detail="Fixtures not found")
    return result


@router.get("/{team_id}/squad", response_model=TeamSquadResponse)
async def get_team_squad(team_id: int = Path(..., gt=0), db: AsyncSession = Depends(get_db)):
    """Get the current squad for a team."""
    result = await football_service.get_team_squad(team_id)
    if not result or "error" in result:
        raise HTTPException(status_code=404, detail="Squad not found")
    return result


@router.get("/{team_id}/matches", response_model=list[MatchResponse])
async def get_team_matches(team_id: int = Path(..., gt=0), db: AsyncSession = Depends(get_db)):
    """Get matches for a team."""
    team_result = await db.execute(select(Team).where(Team.team_id == team_id))
    team = team_result.scalar_one_or_none()
    if team is None:
        raise HTTPException(status_code=404, detail="Team not found")

    result = await football_service.get_team_matches(db, team_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Matches not found")
    return result


@router.get("/{team_id}/finished-matches", response_model=list[MatchResponse])
async def get_team_finished_matches(team_id: int = Path(..., gt=0), db: AsyncSession = Depends(get_db)):
    """Get finished matches for a team."""
    team_result = await db.execute(select(Team).where(Team.team_id == team_id))
    team = team_result.scalar_one_or_none()
    if team is None:
        raise HTTPException(status_code=404, detail="Team not found")

    result = await football_service.get_team_finished_matches(db, team_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Finished matches not found")
    return result


@router.get("/{team_id}/statistics/{league_id}/{season}", response_model=TeamStatisticsResponse)
async def get_team_statistics(
    team_id: int = Path(..., gt=0),
    league_id: int = Path(..., gt=0),
    season: int = Path(..., gt=0),
    db: AsyncSession = Depends(get_db),
):
    """Get normalized team statistics for a team, league, and season."""
    result = await football_service.get_team_statistics(team_id, league_id, season)
    if not result or "error" in result:
        raise HTTPException(status_code=404, detail="Statistics not found")
    return result


@router.get("/{team_id}/standings", response_model=list[StandingResponse])
async def get_team_standings(team_id: int = Path(..., gt=0), db: AsyncSession = Depends(get_db)):
    """Get standings for a team using its resolved Team Context."""
    result = await football_service.get_team_profile_standings(db, team_id)
    if not result:
        raise HTTPException(status_code=404, detail="Standings not found")
    return result


@router.get("/{team_id}", response_model=TeamSchema)
async def get_team_details(team_id: int, db: AsyncSession = Depends(get_db)):
    """Get team profile data"""
    cache_key = make_cache_key("team", team_id)
    cached = await cache_get_json(cache_key)
    if cached is not None:
        return cached

    # Check DB first
    result = await db.execute(select(Team).where(Team.team_id == team_id))
    team = result.scalar_one_or_none()
    if team:
        logger.info(f"Team {team_id} fetched from DB")
        payload = TeamSchema.from_orm(team).dict()
        await cache_set_json(cache_key, payload, settings.REDIS_TTL_LEAGUE_TEAM)
        return payload

    # Fetch from API
    result = await football_service.get_team_details(team_id)
    if not result or "response" not in result or not result["response"]:
        raise HTTPException(status_code=404, detail="Team not found")

    team_data = result["response"][0]
    upserted_team = await football_service.upsert_team(db, team_data)
    await db.commit()
    logger.info(f"Team {team_id} fetched from API and cached")
    payload = TeamSchema.from_orm(upserted_team).dict()
    await cache_set_json(cache_key, payload, settings.REDIS_TTL_LEAGUE_TEAM)
    return payload