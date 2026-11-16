import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, status
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_active_admin
from app.db import get_db
from app.providers.player_provider import PlayerProvider
from app.services.football import football_service
from app.services.player_master_rebuild_service import PlayerMasterRebuildService
from app.services.player_sync_service import (
    PlayerIdentityResolutionError,
    PlayerSyncService,
)
from app.services.resource_lock import (
    ResourceLockAcquisitionError,
    run_with_resource_lock,
)

router = APIRouter()
logger = logging.getLogger(__name__)


class SquadPhotoSyncResponse(BaseModel):
    success: bool
    team_id: int
    provider_team_id: str
    players_collected: int
    photos_inserted: int
    photos_updated: int
    existing_photos_preserved: int
    players_skipped: int
    invalid_photos: int
    missing_photos: int


def build_player_master_rebuild_service() -> PlayerMasterRebuildService:
    player_provider = PlayerProvider(football_service.client)
    player_sync_service = PlayerSyncService(
        player_provider=player_provider,
        client=football_service.client,
    )
    return PlayerMasterRebuildService(
        league_provider=football_service.league_provider,
        team_provider=football_service.team_provider,
        player_provider=player_provider,
        team_sync_service=football_service.team_sync_service,
        player_sync_service=player_sync_service,
    )


@router.post(
    "/players/squad-photos/sync/{team_id}",
    response_model=SquadPhotoSyncResponse,
)
async def sync_team_squad_photos(
    team_id: int = Path(..., gt=0),
    db: AsyncSession = Depends(get_db),
    _admin=Depends(current_active_admin),
) -> dict[str, Any]:
    """Explicitly sync one team's Squad photos into Player Master."""
    service = build_player_master_rebuild_service()

    async def sync() -> dict[str, Any]:
        try:
            result = await service.sync_team_squad_photos(db, team_id)
            await db.commit()
            return result
        except Exception:
            await db.rollback()
            raise

    try:
        locked, result = await run_with_resource_lock(
            db,
            "player_squad_photo",
            team_id,
            sync,
        )
    except ResourceLockAcquisitionError:
        await db.rollback()
        raise
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except PlayerIdentityResolutionError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except ConnectionError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except IntegrityError as exc:
        logger.exception("Squad photo sync identity conflict", extra={"team_id": team_id})
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Squad photo sync encountered a database identity conflict",
        ) from exc
    except SQLAlchemyError as exc:
        logger.exception("Squad photo sync database failure", extra={"team_id": team_id})
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Squad photo sync database operation failed",
        ) from exc
    except Exception as exc:
        logger.exception("Squad photo sync failed", extra={"team_id": team_id})
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Squad photo sync failed",
        ) from exc

    if not locked:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Squad photo sync already in progress",
        )
    return result
