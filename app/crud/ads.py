from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List

from app.models.ad import Ad
from app.schemas.ad import AdCreate


async def get_active_ads(db: AsyncSession) -> List[Ad]:
    result = await db.execute(select(Ad).where(Ad.active == True))
    return result.scalars().all()


async def create_ad(db: AsyncSession, ad: AdCreate) -> Ad:
    db_ad = Ad(**ad.model_dump())
    db.add(db_ad)
    await db.commit()
    await db.refresh(db_ad)
    return db_ad