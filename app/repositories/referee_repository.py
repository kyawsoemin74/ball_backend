from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.referee import Referee


class RefereeRepository:
    async def get_by_id(self, db: AsyncSession, referee_id: int) -> Optional[Referee]:
        result = await db.execute(select(Referee).where(Referee.referee_id == referee_id))
        return result.scalar_one_or_none()

    async def get_by_provider_id(
        self,
        db: AsyncSession,
        provider_id: str,
        provider: str = "api-football",
    ) -> Optional[Referee]:
        if not provider_id:
            return None
        result = await db.execute(
            select(Referee).where(
                (Referee.provider == provider) & (Referee.provider_id == provider_id)
            )
        )
        return result.scalar_one_or_none()

    async def get_by_normalized_name(
        self,
        db: AsyncSession,
        normalized_name: str,
        provider: str = "api-football",
    ) -> Optional[Referee]:
        if not normalized_name:
            return None
        result = await db.execute(
            select(Referee).where(
                (Referee.provider == provider) & (Referee.normalized_name == normalized_name)
            )
        )
        return result.scalar_one_or_none()

    async def get_all(self, db: AsyncSession) -> list[Referee]:
        result = await db.execute(select(Referee).order_by(Referee.referee_id))
        return list(result.scalars().all())

    async def create(self, db: AsyncSession, referee_data: dict) -> Referee:
        row = Referee(**referee_data)
        db.add(row)
        await db.flush()
        return row

    async def update(self, db: AsyncSession, referee_id: int, update_data: dict) -> Referee:
        await db.execute(
            update(Referee)
            .where(Referee.referee_id == referee_id)
            .values(**update_data)
        )
        refreshed = await self.get_by_id(db, referee_id)
        if refreshed is None:
            raise RuntimeError(f"Referee {referee_id} not found after update")
        return refreshed

    async def upsert_one(self, db: AsyncSession, referee_data: dict) -> Referee:
        provider = referee_data.get("provider", "api-football")
        provider_id = referee_data.get("provider_id")
        normalized_name = referee_data.get("normalized_name")

        if not normalized_name:
            raise ValueError("normalized_name is required for referee upsert")

        if provider_id:
            existing = await self.get_by_provider_id(db, provider_id, provider)
            if existing is not None:
                update_values = {
                    key: value
                    for key, value in referee_data.items()
                    if key not in {"referee_id", "created_at", "updated_at", "provider", "provider_id"}
                    and value is not None
                }
                if update_values:
                    await self.update(db, existing.referee_id, update_values)
                return existing

        existing = await self.get_by_normalized_name(db, normalized_name, provider)
        if existing is not None:
            update_values = {
                key: value
                for key, value in referee_data.items()
                if key not in {"referee_id", "created_at", "updated_at", "provider", "provider_id"}
                and value is not None
            }
            if update_values:
                await self.update(db, existing.referee_id, update_values)
            return existing

        return await self.create(db, referee_data)

    async def count(self, db: AsyncSession) -> int:
        result = await db.execute(select(Referee.referee_id))
        return len(result.scalars().all())
