from fastapi import APIRouter, Depends, HTTPException, status, Path, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Optional, Dict, Any
from datetime import datetime, date, timedelta, timezone

from app.api.deps import current_active_admin, current_active_user
from app.cache import cache_get_json, cache_set_json, make_cache_key
from app.core.config import settings
from app.db import get_db
from app.models.league import League
from app.models.match import Match
from app.models.match_event import MatchEvent
from app.models.match_lineup import MatchLineup
from app.models.standing import Standings
from app.repositories.match_repository import MatchRepository
from app.schemas.match_event import MatchEventResponse
from app.schemas.match import MatchDateResponse, MatchResponse
from app.services.football import football_service, LIVE_STATUSES

router = APIRouter(prefix="/matches", tags=["matches"])


def _has_availability_data(payload: Any) -> bool:
    """Return True when the payload contains real cached/API data instead of an empty or error response."""
    if payload is None:
        return False

    if isinstance(payload, dict):
        if payload.get("error"):
            return False

        for key in ("response", "odds", "stats", "fixtures", "data", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                return len(value) > 0
            if isinstance(value, dict):
                return bool(value)

        return bool(payload.get("cached") or payload.get("source") in {"database", "api"})

    if isinstance(payload, (list, tuple, set)):
        return len(payload) > 0

    return bool(payload)


async def _build_match_availability_flags(match: Match, db: AsyncSession) -> Dict[str, bool]:
    """Compute match detail tab availability flags from existing cached data sources."""
    flags = {
        "has_events": False,
        "has_stats": False,
        "has_lineups": False,
        "has_odds": False,
        "has_h2h": False,
        "has_standings": False,
        "has_predictions": False,
        "has_rankings": False,
        "has_news": False,
        "has_highlights": False,
        "has_comments": False,
        "is_knockout": False,
        "has_bracket": False,
    }

    events_result = await db.execute(select(MatchEvent).where(MatchEvent.match_id == match.match_id).limit(1))
    flags["has_events"] = events_result.scalar_one_or_none() is not None

    try:
        stats_payload = await football_service.get_cached_statistics(db, match.match_id)
    except Exception:
        stats_payload = {}
    flags["has_stats"] = _has_availability_data(stats_payload)

    lineup_result = await db.execute(select(MatchLineup).where(MatchLineup.match_id == match.match_id).limit(1))
    flags["has_lineups"] = lineup_result.scalar_one_or_none() is not None

    try:
        odds_payload = await football_service.get_cached_odds(db, match.match_id)
    except Exception:
        odds_payload = {}
    flags["has_odds"] = _has_availability_data(odds_payload)

    if match.home_team_id and match.away_team_id:
        try:
            h2h_payload = await football_service.get_cached_h2h(db, match.home_team_id, match.away_team_id, match.match_id)
        except Exception:
            h2h_payload = {}
        flags["has_h2h"] = _has_availability_data(h2h_payload)

    league = await db.get(League, match.league_id)
    season = getattr(league, "season", None) if league else None
    if season:
        standings_result = await db.execute(
            select(Standings)
            .where(Standings.league_id == match.league_id, Standings.season == str(season))
            .limit(1)
        )
        flags["has_standings"] = standings_result.scalar_one_or_none() is not None

    league_name = (match.league_name or "").lower()
    if any(keyword in league_name for keyword in ("cup", "knockout", "playoff", "final", "semi")):
        flags["is_knockout"] = True

    return flags


# --- GET Routes (Specific Routes First) ---

@router.get("/live_all", response_model=List[MatchResponse])
async def get_all_live_matches(
    db: AsyncSession = Depends(get_db),
):
    """
    Return cached active live matches from the database.
    Live data is kept fresh by the background scheduler polling every 2400 seconds (40 minutes).
    """
    cache_key = make_cache_key("live_matches")
    cached = await cache_get_json(cache_key)
    if cached is not None:
        return cached

    result = await db.execute(select(Match).where(Match.status.in_(LIVE_STATUSES)))
    live_matches = result.scalars().all()
    payload = [MatchResponse.model_validate(match).model_dump(mode="json") for match in live_matches]
    await cache_set_json(cache_key, payload, settings.REDIS_TTL_LIVE_MATCHES)
    return payload


@router.get("/", response_model=List[MatchResponse])
async def get_all_matches(
    status: Optional[str] = Query(None, description="Filter by status (e.g., FT, NS)"),
    league_id: Optional[int] = Query(None, description="Filter by league ID"),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, le=500),
    db: AsyncSession = Depends(get_db)
):
    """
    ပွဲစဉ်အားလုံးကို Filter အသုံးပြု၍ ရယူရန်။
    """
    query = select(Match)
    if status:
        query = query.where(Match.status == status)
    if league_id:
        query = query.where(Match.league_id == league_id)

    result = await db.execute(query.offset(skip).limit(limit))
    return result.scalars().all()


@router.get("/{match_id}", response_model=MatchResponse)
async def get_match_by_id(
    match_id: int = Path(..., description="The unique match ID of the match", gt=0),
    db: AsyncSession = Depends(get_db)
):
    """
    Match ID အသုံးပြုပြီး ပွဲစဉ်အသေးစိတ်ကို ရယူရန်။
    (Note: match_id သည် integer ဖြစ်ရပါမည်။)
    """
    result = await db.execute(select(Match).where(Match.match_id == match_id))
    match = result.scalar_one_or_none()

    if not match:
        raise HTTPException(status_code=404, detail="Match not found")

    availability_flags = await _build_match_availability_flags(match, db)
    response = MatchResponse.model_validate(match)
    return response.model_copy(update=availability_flags)


@router.get("/date/{date_val}", response_model=List[MatchDateResponse])
async def get_matches_by_date(
    date_val: date = Path(..., description="ရက်စွဲအလိုက် ပွဲစဉ်ရှာရန် (Format: YYYY-MM-DD)"),
    db: AsyncSession = Depends(get_db)
):
    """
    သတ်မှတ်ထားသော ရက်စွဲအလိုက် ပွဲစဉ်များကို ရယူရန်။
    Myanmar Timezone (UTC+6:30) aware - returns matches for the full day in Myanmar time.
    """
    matches = await MatchRepository().get_matches_by_date(db, date_val)
    return MatchRepository.order_matches_for_date(matches)


@router.get("/{match_id}/events", response_model=List[MatchEventResponse])
async def get_match_events(
    match_id: int = Path(..., gt=0),
    db: AsyncSession = Depends(get_db)
):
    """
    Get match events with smart caching and DB persistence for finished matches.
    """
    result = await football_service.get_cached_match_events(db, match_id)
    if not result:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Events not found")
    return result


@router.get("/{match_id}/lineup")
async def get_match_lineup(
    match_id: int = Path(..., gt=0),
    db: AsyncSession = Depends(get_db)
):
    """
    Get match lineup for a specific match.
    """
    result = await football_service.get_cached_match_lineup(db, match_id)
    if not result:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lineup not found")
    return result


@router.get("/h2h/{match_id}/{team1_id}/{team2_id}")
async def get_match_h2h_symmetric(
    match_id: int = Path(..., gt=0),
    team1_id: int = Path(..., gt=0),
    team2_id: int = Path(..., gt=0),
    db: AsyncSession = Depends(get_db)
):
    """
    Get head-to-head statistics with symmetric team key logic and smart persistence.
    """
    result = await football_service.get_cached_h2h(db, team1_id, team2_id, match_id)
    if not result:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="H2H data not found")
    return result


@router.get("/{match_id}/odds")
async def get_match_odds(match_id: int = Path(..., gt=0), db: AsyncSession = Depends(get_db)):
    """
    Get betting odds for a specific match with smart caching (30-min rule, no API after match start).
    """
    # Fallback to Service logic (DB check -> API fetch if needed)
    result = await football_service.get_cached_odds(db, match_id)
    if "error" in result:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=result["error"])

    await db.commit()
    return result


# --- POST/Sync Routes (Grouped Together) ---

@router.post("/sync/{match_id}/events", status_code=status.HTTP_200_OK, dependencies=[Depends(current_active_admin)])
async def sync_match_events(
    match_id: int = Path(..., gt=0),
    db: AsyncSession = Depends(get_db)
) -> Dict[str, Any]:
    """
    Finalized ဖြစ်သွားသော ပွဲစဉ်အတွက် Events များကို API မှ ဆွဲယူပြီး Database တွင် သိမ်းဆည်းရန်။
    """
    result = await football_service.sync_match_events(db=db, match_id=match_id)
    await db.commit()
    return result


@router.post("/sync/season", status_code=status.HTTP_200_OK, dependencies=[Depends(current_active_admin)])
async def sync_full_season(
    league_id: int = Query(39, description="The league ID to sync"),
    season: int = Query(2026, description="The season year"),
    db: AsyncSession = Depends(get_db)
) -> Dict[str, Any]:
    """
    သတ်မှတ်ထားသော League နှင့် Season တစ်ခုလုံးအတွက် ပွဲစဉ်များကို Sync လုပ်ရန်။
    """
    result = await football_service.sync_full_season(db=db, league=league_id, season=season)
    await db.commit()
    return result


@router.post("/sync/{date_val}", status_code=status.HTTP_200_OK, dependencies=[Depends(current_active_admin)])
async def sync_daily_matches(
    date_val: date = Path(..., description="The date to sync (YYYY-MM-DD)"),
    db: AsyncSession = Depends(get_db)
) -> Dict[str, Any]:
    """
    သတ်မှတ်ထားသော ရက်စဉ်အတွက် ပွဲစဉ်များကို API မှ ဆွဲယူပြီး Database တွင် သိမ်းဆည်းရန်။
    """
    result = await football_service.sync_daily_fixtures(db=db, target_date=date_val.isoformat())
    await db.commit()
    return result
