import logging

from fastapi import APIRouter, Depends, HTTPException, status, Path, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Optional, Dict, Any
from datetime import date

from app.api.deps import current_active_admin
from app.cache import cache_get_json, cache_set_json, make_cache_key
from app.core.config import settings
from app.db import get_db
from app.models.league import League
from app.models.match import Match
from app.models.match_event import MatchEvent
from app.models.match_h2h import MatchH2H
from app.models.match_lineup import MatchLineup
from app.models.odds import Odds
from app.repositories.allowed_league_repository import AllowedLeagueRepository
from app.repositories.match_repository import MatchRepository
from app.schemas.match import MatchDateResponse, MatchResponse, MatchStatisticsResponse
from app.services.league_structure_resolver import LeagueStructureResolver
from app.services.active_match_service import active_match_service
from app.services.football import football_service, LIVE_STATUSES

router = APIRouter(prefix="/matches", tags=["matches"])
league_structure_resolver = LeagueStructureResolver()
logger = logging.getLogger(__name__)


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
    if events_result.scalar_one_or_none() is not None:
        logger.info("HAS_EVENTS_DB_HIT", extra={"match_id": match.match_id})
        flags["has_events"] = True
    else:
        logger.info("HAS_EVENTS_FALLBACK_CHECK", extra={"match_id": match.match_id})
        # Keep EventService as the single source of truth for event availability across all statuses.
        try:
            cached_or_fresh_events = await football_service.get_cached_match_events(db, match.match_id)
        except Exception:
            cached_or_fresh_events = []

        flags["has_events"] = bool(cached_or_fresh_events)
        if flags["has_events"]:
            logger.info("HAS_EVENTS_FALLBACK_TRUE", extra={"match_id": match.match_id})
        else:
            logger.info("HAS_EVENTS_FALLBACK_FALSE", extra={"match_id": match.match_id})

    try:
        stats_payload = await football_service.get_cached_statistics(db, match.match_id)
    except Exception:
        stats_payload = {}
    flags["has_stats"] = _has_availability_data(stats_payload)

    lineup_result = await db.execute(select(MatchLineup).where(MatchLineup.match_id == match.match_id).limit(1))
    flags["has_lineups"] = lineup_result.scalar_one_or_none() is not None

    flags["has_odds"] = await _has_odds_available(match.match_id, db)
    flags["has_h2h"] = await _has_h2h_available(match.home_team_id, match.away_team_id, match.match_id, db)

    structure = await league_structure_resolver.resolve(match, db)
    flags["has_standings"] = structure.has_standings
    flags["is_knockout"] = structure.is_knockout
    flags["has_bracket"] = structure.has_bracket

    return flags


async def _has_odds_available(match_id: int, db: AsyncSession) -> bool:
    odds_result = await db.execute(select(Odds).where(Odds.fixture_id == match_id).limit(1))
    if odds_result.scalar_one_or_none() is not None:
        return True

    try:
        odds_payload = await football_service.get_cached_odds(db, match_id)
    except Exception:
        odds_payload = {}
    return _has_availability_data(odds_payload)


def _build_h2h_key(home_team_id: int, away_team_id: int) -> str:
    ids = sorted([home_team_id, away_team_id])
    return f"{ids[0]}-{ids[1]}"


async def _has_h2h_available(home_team_id: int | None, away_team_id: int | None, match_id: int, db: AsyncSession) -> bool:
    if not home_team_id or not away_team_id:
        return False

    h2h_key = _build_h2h_key(home_team_id, away_team_id)
    h2h_result = await db.execute(select(MatchH2H).where(MatchH2H.h2h_key == h2h_key).limit(1))
    if h2h_result.scalar_one_or_none() is not None:
        return True

    try:
        h2h_payload = await football_service.get_cached_h2h(db, home_team_id, away_team_id, match_id)
    except Exception:
        h2h_payload = {}
    return _has_availability_data(h2h_payload)


# --- GET Routes (Specific Routes First) ---

@router.get("/live_all", response_model=List[MatchResponse])
async def get_all_live_matches(
    db: AsyncSession = Depends(get_db),
):
    """
    Return cached active live matches from the database.
    Live data is kept fresh by the background scheduler polling every 2400 seconds (40 minutes).
    """
    allowed_ids = await AllowedLeagueRepository().get_allowed_ids(db)
    cache_key = make_cache_key("live_matches")
    cached = await cache_get_json(cache_key)
    if cached is not None:
        if not allowed_ids:
            return []
        return [item for item in cached if item.get("league_id") in allowed_ids]

    live_matches = await MatchRepository().get_live_matches(db, allowed_ids)
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
    allowed_ids = await AllowedLeagueRepository().get_allowed_ids(db)
    matches = await MatchRepository().get_all_matches(db, allowed_ids=allowed_ids, status=status, league_id=league_id, skip=skip, limit=limit)
    return matches


async def _assert_match_allowed(match_id: int, db: AsyncSession) -> Match:
    allowed_ids = await AllowedLeagueRepository().get_allowed_ids(db)
    match = await MatchRepository().get_by_id(db, match_id, allowed_ids)
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")
    return match


@router.get("/{match_id}", response_model=MatchResponse)
async def get_match_by_id(
    match_id: int = Path(..., description="The unique match ID of the match", gt=0),
    db: AsyncSession = Depends(get_db)
):
    """
    Match ID အသုံးပြုပြီး ပွဲစဉ်အသေးစိတ်ကို ရယူရန်။
    (Note: match_id သည် integer ဖြစ်ရပါမည်။)
    """
    match = await _assert_match_allowed(match_id, db)
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
    allowed_ids = await AllowedLeagueRepository().get_allowed_ids(db)
    matches = await MatchRepository().get_matches_by_date(db, date_val, allowed_ids)
    return MatchRepository.order_matches_for_date(matches)


@router.get("/{match_id}/events")
async def get_match_events(
    match_id: int = Path(..., gt=0),
    db: AsyncSession = Depends(get_db)
):
    """
    Get match events with smart caching and DB persistence for finished matches.
    """
    await _assert_match_allowed(match_id, db)
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
    await _assert_match_allowed(match_id, db)
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
    await _assert_match_allowed(match_id, db)
    result = await football_service.get_cached_h2h(db, team1_id, team2_id, match_id)
    if not result:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="H2H data not found")
    return result


@router.get("/{match_id}/statistics", response_model=MatchStatisticsResponse)
async def get_match_statistics(
    match_id: int = Path(..., gt=0),
    db: AsyncSession = Depends(get_db),
):
    """
    Get normalized match statistics for a specific match using the existing cached API-Football data.
    """
    await _assert_match_allowed(match_id, db)
    result = await football_service.get_normalized_statistics(db, match_id)
    if not result or "error" in result:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Statistics not found")
    return result


@router.get("/{match_id}/odds")
async def get_match_odds(match_id: int = Path(..., gt=0), db: AsyncSession = Depends(get_db)):
    """
    Get betting odds for a specific match with smart caching (30-min rule, no API after match start).
    """
    # Fallback to Service logic (DB check -> API fetch if needed)
    await _assert_match_allowed(match_id, db)
    result = await football_service.get_cached_odds(db, match_id)
    if "error" in result:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=result["error"])

    await db.commit()
    return result


@router.post("/{match_id}/heartbeat")
async def heartbeat_match(
    match_id: int = Path(..., gt=0),
    db: AsyncSession = Depends(get_db),
):
    """
    Mark a match as actively viewed and refresh the active-match TTL.
    """
    await _assert_match_allowed(match_id, db)
    ttl_seconds = await active_match_service.mark_match_active(match_id)
    return {"success": True, "match_id": match_id, "ttl_seconds": ttl_seconds}


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
