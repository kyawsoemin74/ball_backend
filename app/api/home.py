from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.services.home_service import HomeService

router = APIRouter()


@router.get("/home")
async def get_home(db: AsyncSession = Depends(get_db)):
    """Return the home screen aggregation payload for the frontend."""
    return await HomeService().get_home_payload(db)
