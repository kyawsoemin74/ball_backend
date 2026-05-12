from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
import logging

from app.db import get_db
from app.models.team import Team
from app.schemas.team import Team as TeamSchema
from app.services.football import football_service

router = APIRouter()
logger = logging.getLogger(__name__)

@router.get("/{team_id}", response_model=TeamSchema)
async def get_team_details(team_id: int, db: Session = Depends(get_db)):
    """Get team profile data"""
    # Check DB first
    team = db.query(Team).filter(Team.team_id == team_id).first()
    if team:
        logger.info(f"Team {team_id} fetched from DB")
        return team

    # Fetch from API
    result = await football_service.get_team_details(team_id)
    if not result or "response" not in result or not result["response"]:
        raise HTTPException(status_code=404, detail="Team not found")

    team_data = result["response"][0]
    upserted_team = football_service.upsert_team(db, team_data)
    logger.info(f"Team {team_id} fetched from API and cached")
    return upserted_team