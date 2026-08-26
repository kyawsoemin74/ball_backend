from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ad_config import AdConfig


class AdRepository:
    async def get_current_config(self, db: AsyncSession) -> AdConfig | None:
        result = await db.execute(select(AdConfig).order_by(AdConfig.id.desc()).limit(1))
        return result.scalar_one_or_none()

    async def update_current_config(self, db: AsyncSession, config: AdConfig) -> AdConfig:
        db.add(config)
        await db.flush()
        await db.refresh(config)
        return config
