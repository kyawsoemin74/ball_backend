from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List, Optional, Dict, Any
from datetime import datetime, date, timedelta

from app.db import get_db
from app.models.match import Match
from app.schemas.match import MatchResponse
from app.services.football import football_service 
from app.api.deps import api_key_security # Import the new API key dependency

router = APIRouter(prefix="/matches", tags=["matches"])

@router.get("/live_all", response_model=List[MatchResponse])
def get_all_live_matches(
    db: Session = Depends(get_db),
    api_key: str = Depends(api_key_security) # Apply the API key dependency here
):
    live_statuses = ["1H", "2H", "HT", "ET", "LIVE"]
    matches = db.query(Match).filter(
        Match.status.in_(live_statuses)
    ).all()
    return matches

@router.get("/{match_id}", response_model=MatchResponse)
def get_match_by_id(match_id: int, db: Session = Depends(get_db)):
    match = db.query(Match).filter(
        Match.fixture_id == match_id
    ).first()
    
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")
    
    return match

@router.get("/{date_val}", response_model=List[MatchResponse])
def get_matches_by_date(date_val: date, db: Session = Depends(get_db)):
    """
    Get matches for the requested date (YYYY-MM-DD).
    """
    start_dt = datetime.combine(date_val, datetime.min.time())
    end_dt = start_dt + timedelta(days=1)

    matches = db.query(Match).filter(
        Match.match_time >= start_dt,
        Match.match_time < end_dt
    ).all()
    return matches

@router.get("/", response_model=List[MatchResponse])
def get_all_matches(
    status: Optional[str] = None,
    league_id: Optional[int] = None,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db)
):
    query = db.query(Match)
    
    if status:
        query = query.filter(Match.status == status)
    
    if league_id:
        query = query.filter(Match.league_id == league_id)
    
    matches = query.offset(skip).limit(limit).all()
    return matches

@router.post("/sync/season", dependencies=[Depends(api_key_security)])
async def sync_full_season(
    league_id: int = 39,
    season: int = 2026,
    db: Session = Depends(get_db)
) -> Dict[str, Any]:
    """
    Sync all fixtures for a specific league and season.
    """
    result = await football_service.sync_full_season(db=db, league=league_id, season=season)
    return result

@router.post("/sync/{date_val}", dependencies=[Depends(api_key_security)])
async def sync_daily_matches(
    date_val: date,
    db: Session = Depends(get_db)
) -> Dict[str, Any]:
    """
    Sync fixtures for a specific date (Format: YYYY-MM-DD) from the path.
    """
    result = await football_service.sync_daily_fixtures(db=db, target_date=date_val.isoformat())
    return result

@router.get("/{match_id}/events")
async def get_match_events(match_id: int):
    """
    Get match events (goals, cards, substitutions) for a specific match.
    """
    result = await football_service.get_match_events(match_id)
    if not result:
        raise HTTPException(status_code=404, detail="Events not found")
    return result

@router.get("/{match_id}/lineup")
async def get_match_lineup(match_id: int):
    """
    Get match lineup for a specific match.
    """
    result = await football_service.get_match_lineup(match_id)
    if not result:
        raise HTTPException(status_code=404, detail="Lineup not found")
    return result

@router.get("/{match_id}/h2h")
async def get_match_h2h(match_id: int):
    """
    Get head-to-head statistics for a specific match.
    """
    result = await football_service.get_match_h2h(match_id)
    if not result:
        raise HTTPException(status_code=404, detail="H2H data not found")
    return result

@router.get("/{match_id}/odds")
async def get_match_odds(match_id: int):
    """
    Get betting odds for a specific match.
    """
    result = await football_service.get_match_odds(match_id)
    if not result:
        raise HTTPException(status_code=404, detail="Odds not found")
    return result