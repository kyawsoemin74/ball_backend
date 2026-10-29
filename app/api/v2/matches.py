from fastapi import APIRouter, Depends, Path
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.matches import _assert_match_allowed
from app.db import get_db
from app.schemas.h2h_v2 import H2HResponseV2
from app.services.football import football_service
from app.services.h2h_service import H2HTeamIdentityUnavailable


router = APIRouter(prefix="/matches", tags=["matches-v2"])


@router.get(
    "/h2h/{match_id}/{team1_id}/{team2_id}",
    response_model=H2HResponseV2,
)
async def get_normalized_match_h2h(
    match_id: int = Path(..., gt=0),
    team1_id: int = Path(..., gt=0),
    team2_id: int = Path(..., gt=0),
    db: AsyncSession = Depends(get_db),
):
    match = await _assert_match_allowed(match_id, db)
    home_team_id = getattr(match, "home_team_id", None)
    away_team_id = getattr(match, "away_team_id", None)
    if (
        home_team_id is None
        or away_team_id is None
        or int(home_team_id) == int(away_team_id)
        or team1_id == team2_id
        or {team1_id, team2_id} != {int(home_team_id), int(away_team_id)}
    ):
        return JSONResponse(
            status_code=422,
            content={
                "detail": "Supplied team IDs do not match the local match",
                "code": "H2H_TEAM_IDS_MISMATCH",
            },
        )

    try:
        result = await football_service.get_normalized_h2h(db, match)
    except H2HTeamIdentityUnavailable:
        return JSONResponse(
            status_code=422,
            content={
                "detail": "H2H team identity is unavailable",
                "code": "H2H_TEAM_IDENTITY_UNAVAILABLE",
            },
        )

    if result is None:
        return JSONResponse(
            status_code=404,
            content={"detail": "H2H data not found", "code": "H2H_NOT_FOUND"},
        )
    return result
