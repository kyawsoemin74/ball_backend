from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
import logging

from app.db import get_db
from app.models.league import League
from app.models.standings import Standings
from app.schemas.league import League as LeagueSchema
from app.schemas.standings import StandingsResponse
from app.services.football import football_service

router = APIRouter()
logger = logging.getLogger(__name__)

@router.get("/{league_id}", response_model=LeagueSchema)
async def get_league_details(league_id: int, db: Session = Depends(get_db)):
    """Get league details, caching in DB"""
    # Check DB first
    league = db.query(League).filter(League.league_id == league_id).first()
    if league:
        logger.info(f"League {league_id} fetched from DB")
        return league

    # Fetch from API
    result = await football_service.get_league_details(league_id)
    if not result or "response" not in result or not result["response"]:
        raise HTTPException(status_code=404, detail="League not found")

    league_data = result["response"][0]
    upserted_league = football_service.upsert_league(db, league_data)
    logger.info(f"League {league_id} fetched from API and cached")
    return upserted_league

@router.get("/{league_id}/standings", response_model=StandingsResponse)
async def get_league_standings(league_id: int, season: str = "2023", db: Session = Depends(get_db)):
    """Get league standings, ensuring fresh data"""
    # Check if we have recent standings
    existing = db.query(Standings).filter(
        Standings.league_id == league_id,
        Standings.season == season
    ).first()

    if not existing:
        # Fetch from API
        result = await football_service.get_league_standings(league_id, int(season))
        if not result or "response" not in result or not result["response"]:
            raise HTTPException(status_code=404, detail="Standings not found")

        standings_data = result["response"][0]["league"]["standings"][0]  # Assuming single group
        football_service.upsert_standings(db, standings_data, league_id, season)
        logger.info(f"Standings for league {league_id} season {season} fetched from API and cached")

    standings = db.query(Standings).filter(
        Standings.league_id == league_id,
        Standings.season == season
    ).order_by(Standings.position).all()

    return StandingsResponse(
        league_id=league_id,
        season=season,
        standings=standings
    )