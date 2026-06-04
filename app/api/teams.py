from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
import logging

from app.cache import cache_get_json, cache_set_json, make_cache_key
from app.core.config import settings
from app.db import get_db
from app.models.team import Team
from app.schemas.team import Team as TeamSchema
from app.services.football import football_service

router = APIRouter()
logger = logging.getLogger(__name__)

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