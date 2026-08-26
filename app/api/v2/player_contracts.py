from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models.match_event import MatchEvent
from app.repositories.player_repository import PlayerRepository
from app.schemas.player_v2 import (
    EventResponseV2,
    PlayerV2Response,
    SquadResponseV2,
    TopScorersResponseV2,
)
from app.services.football import football_service

router = APIRouter(tags=["player-v2"])
player_repository = PlayerRepository()


def _serialize_player(player) -> dict[str, Any]:
    return {
        "player_id": player.player_id,
        "provider": player.provider,
        "provider_player_id": player.provider_id,
        "name": player.name,
        "first_name": player.first_name,
        "last_name": player.last_name,
        "nationality": player.nationality,
        "position": player.position,
        "photo": player.photo,
        "created_at": player.created_at,
        "updated_at": player.updated_at,
    }


@router.get("/players/provider/{provider}/{provider_player_id}", response_model=PlayerV2Response)
async def get_player_by_provider_identity(
    provider: str,
    provider_player_id: str,
    db: AsyncSession = Depends(get_db),
):
    player = await player_repository.get_by_provider_id(db, provider_player_id, provider)
    if player is None:
        raise HTTPException(status_code=404, detail="Player not found")
    return _serialize_player(player)


@router.get("/players/{player_id}", response_model=PlayerV2Response)
async def get_player_by_local_id(
    player_id: int = Path(..., gt=0),
    db: AsyncSession = Depends(get_db),
):
    player = await player_repository.get_by_id(db, player_id)
    if player is None:
        raise HTTPException(status_code=404, detail="Player not found")
    return _serialize_player(player)


@router.get("/teams/{team_id}/squad", response_model=SquadResponseV2)
async def get_team_squad_v2(
    team_id: int = Path(..., gt=0),
    db: AsyncSession = Depends(get_db),
):
    payload = await football_service.get_team_squad(db, team_id)
    if not payload or "error" in payload:
        raise HTTPException(status_code=404, detail="Squad not found")

    players = []
    for item in payload.get("players", []):
        provider_player_id = item.get("provider_player_id") or item.get("player_id")
        if provider_player_id is None:
            continue
        provider_player_id = str(provider_player_id)
        master = await player_repository.get_by_provider_id(
            db, provider_player_id, "api-football"
        )
        players.append({
            **item,
            "player_id": master.player_id if master is not None else None,
            "provider_player_id": provider_player_id,
        })
    return {**payload, "players": players}


@router.get("/leagues/{league_id}/topscorers/{season}", response_model=TopScorersResponseV2)
async def get_top_scorers_v2(
    league_id: int = Path(..., gt=0),
    season: int = Path(..., gt=0),
    db: AsyncSession = Depends(get_db),
):
    payload = await football_service.get_league_top_scorers(league_id, season)
    if not payload or "error" in payload:
        raise HTTPException(status_code=404, detail="Top scorers not found")

    players = []
    for item in payload.get("players", []):
        provider_player_id = item.get("provider_player_id") or item.get("player_id")
        if provider_player_id is None:
            continue
        provider_player_id = str(provider_player_id)
        master = await player_repository.get_by_provider_id(
            db, provider_player_id, "api-football"
        )
        players.append({
            **item,
            "player_id": master.player_id if master is not None else None,
            "provider_player_id": provider_player_id,
        })
    return {**payload, "players": players}


@router.get("/matches/{match_id}/lineup")
async def get_match_lineup_v2(
    match_id: int = Path(..., gt=0),
    db: AsyncSession = Depends(get_db),
):
    payload = await football_service.get_cached_match_lineup(db, match_id)
    if not payload:
        raise HTTPException(status_code=404, detail="Lineup not found")

    enriched = []
    for lineup in payload:
        lineup_copy = dict(lineup)
        for section in ("startXI", "substitutes"):
            entries = []
            for entry in lineup_copy.get(section, []):
                entry_copy = dict(entry)
                provider_player = dict(entry_copy.get("player") or {})
                provider_player_id = provider_player.get("id")
                if provider_player_id is not None:
                    provider_player_id = str(provider_player_id)
                    master = await player_repository.get_by_provider_id(
                        db, provider_player_id, "api-football"
                    )
                    provider_player["provider_player_id"] = provider_player_id
                    provider_player["player_id"] = master.player_id if master else None
                entry_copy["player"] = provider_player
                entries.append(entry_copy)
            lineup_copy[section] = entries
        enriched.append(lineup_copy)
    return enriched


@router.get("/matches/{match_id}/events", response_model=list[EventResponseV2])
async def get_match_events_v2(
    match_id: int = Path(..., gt=0),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(MatchEvent).where(MatchEvent.match_id == match_id).order_by(
            MatchEvent.time_elapsed, MatchEvent.time_extra
        )
    )
    events = list(result.scalars().all())
    if not events:
        raise HTTPException(status_code=404, detail="Events not found")
    return events