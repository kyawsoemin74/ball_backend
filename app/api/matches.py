from fastapi import APIRouter, Depends, HTTPException, status, Path, Query
from sqlalchemy.orm import Session
from typing import List, Optional, Dict, Any
from datetime import datetime, date, timedelta, timezone

from app.api.deps import api_key_security
from app.db import get_db
from app.models.match import Match
from app.schemas.match import MatchResponse
from app.services.football import football_service, LIVE_STATUSES

router = APIRouter(prefix="/matches", tags=["matches"])

# --- GET Routes (Specific Routes First) ---

@router.get("/live_all", response_model=List[MatchResponse])
def get_all_live_matches(
    db: Session = Depends(get_db),
    api_key: str = Depends(api_key_security)
):
    """
    Return cached active live matches from the database.
    Live data is kept fresh by the background scheduler polling every 2400 seconds (40 minutes).
    """
    live_matches = db.query(Match).filter(Match.status.in_(LIVE_STATUSES)).all()
    return live_matches

@router.get("/", response_model=List[MatchResponse])
def get_all_matches(
    status: Optional[str] = Query(None, description="Filter by status (e.g., FT, NS)"),
    league_id: Optional[int] = Query(None, description="Filter by league ID"),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, le=500),
    db: Session = Depends(get_db)
):
    """
    ပွဲစဉ်အားလုံးကို Filter အသုံးပြု၍ ရယူရန်။
    """
    query = db.query(Match)
    if status:
        query = query.filter(Match.status == status)
    if league_id:
        query = query.filter(Match.league_id == league_id)
    
    return query.offset(skip).limit(limit).all()

# --- Match Detail Routes ---

@router.get("/{match_id}", response_model=MatchResponse)
def get_match_by_id(
    match_id: int = Path(..., description="The unique match ID of the match", gt=0),
    db: Session = Depends(get_db)
):
    """
    Match ID အသုံးပြုပြီး ပွဲစဉ်အသေးစိတ်ကို ရယူရန်။
    (Note: match_id သည် integer ဖြစ်ရပါမည်။)
    """
    match = db.query(Match).filter(
        Match.match_id == match_id
    ).first()
    
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")
    
    return match

@router.get("/date/{date_val}", response_model=List[MatchResponse])
def get_matches_by_date(
    date_val: date = Path(..., description="ရက်စွဲအလိုက် ပွဲစဉ်ရှာရန် (Format: YYYY-MM-DD)"),
    db: Session = Depends(get_db)
):
    """
    သတ်မှတ်ထားသော ရက်စွဲအလိုက် ပွဲစဉ်များကို ရယူရန်။
    Myanmar Timezone (UTC+6:30) aware - returns matches for the full day in Myanmar time.
    """
    # Calculate start UTC: beginning of day in Myanmar time minus 6:30 hours
    start_dt = (datetime.combine(date_val, datetime.min.time()) - timedelta(hours=6, minutes=30)).replace(tzinfo=timezone.utc)
    end_dt = start_dt + timedelta(days=1)

    matches = db.query(Match).filter(
        Match.match_time >= start_dt,
        Match.match_time < end_dt
    ).all()
    return matches

@router.get("/{match_id}/events")
async def get_match_events(match_id: int = Path(..., gt=0)):
    """
    Get match events (goals, cards, substitutions) for a specific match.
    """
    result = await football_service.get_match_events(match_id)
    if not result:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Events not found")
    return result

@router.get("/{match_id}/lineup")
async def get_match_lineup(match_id: int = Path(..., gt=0)):
    """
    Get match lineup for a specific match.
    """
    result = await football_service.get_match_lineup(match_id)
    if not result:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lineup not found")
    return result

@router.get("/{match_id}/h2h")
async def get_match_h2h(match_id: int = Path(..., gt=0)):
    """
    Get head-to-head statistics for a specific match.
    """
    result = await football_service.get_match_h2h(match_id)
    if not result:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="H2H data not found")
    return result

@router.get("/{match_id}/odds")
async def get_match_odds(match_id: int = Path(..., gt=0), db: Session = Depends(get_db)):
    """
    Get betting odds for a specific match with smart caching (30-min rule, no API after match start).
    """
    result = await football_service.get_cached_odds(db, match_id)
    if "error" in result:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=result["error"])
    return result

# --- POST/Sync Routes (Grouped Together) ---

@router.post("/sync/season", status_code=status.HTTP_200_OK, dependencies=[Depends(api_key_security)])
async def sync_full_season(
    league_id: int = Query(39, description="The league ID to sync"),
    season: int = Query(2026, description="The season year"),
    db: Session = Depends(get_db)
) -> Dict[str, Any]:
    """
    သတ်မှတ်ထားသော League နှင့် Season တစ်ခုလုံးအတွက် ပွဲစဉ်များကို Sync လုပ်ရန်။
    """
    return await football_service.sync_full_season(db=db, league=league_id, season=season)

@router.post("/sync/{date_val}", status_code=status.HTTP_200_OK, dependencies=[Depends(api_key_security)])
async def sync_daily_matches(
    date_val: date = Path(..., description="The date to sync (YYYY-MM-DD)"),
    db: Session = Depends(get_db)
) -> Dict[str, Any]:
    """
    သတ်မှတ်ထားသော ရက်စွဲအတွက် ပွဲစဉ်များကို API မှ ဆွဲယူပြီး Database တွင် သိမ်းဆည်းရန်။
    """
    return await football_service.sync_daily_fixtures(db=db, target_date=date_val.isoformat())