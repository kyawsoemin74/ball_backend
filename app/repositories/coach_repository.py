from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.coach import Coach


class CoachRepository:
    async def get_by_id(self, db: AsyncSession, coach_id: int) -> Optional[Coach]:
        result = await db.execute(select(Coach).where(Coach.coach_id == coach_id))
        return result.scalar_one_or_none()

    async def get_by_provider_id(
        self,
        db: AsyncSession,
        provider_id: str,
        provider: str = "api-football",
    ) -> Optional[Coach]:
        if not provider_id:
            return None
        result = await db.execute(
            select(Coach).where(
                (Coach.provider == provider) & (Coach.provider_id == provider_id)
            )
        )
        return result.scalar_one_or_none()

    async def get_by_normalized_name(
        self,
        db: AsyncSession,
        normalized_name: str,
        provider: str = "api-football",
    ) -> Optional[Coach]:
        if not normalized_name:
            return None
        result = await db.execute(
            select(Coach).where(
                (Coach.provider == provider) & (Coach.normalized_name == normalized_name)
            )
        )
        return result.scalar_one_or_none()

    async def get_all(self, db: AsyncSession) -> list[Coach]:
        result = await db.execute(select(Coach).order_by(Coach.coach_id))
        return list(result.scalars().all())

    async def create(self, db: AsyncSession, coach_data: dict) -> Coach:
        row = Coach(**coach_data)
        db.add(row)
        await db.flush()
        return row

    async def update(self, db: AsyncSession, coach_id: int, update_data: dict) -> Coach:
        await db.execute(
            update(Coach)
            .where(Coach.coach_id == coach_id)
            .values(**update_data)
        )
        refreshed = await self.get_by_id(db, coach_id)
        if refreshed is None:
            raise RuntimeError(f"Coach {coach_id} not found after update")
        return refreshed

    async def upsert_one(self, db: AsyncSession, coach_data: dict) -> Coach:
        provider = coach_data.get("provider", "api-football")
        provider_id = coach_data.get("provider_id")
        normalized_name = coach_data.get("normalized_name")

        if not normalized_name:
            raise ValueError("normalized_name is required for coach upsert")

        if provider_id:
            existing = await self.get_by_provider_id(db, provider_id, provider)
            if existing is not None:
                update_values = {
                    key: value
                    for key, value in coach_data.items()
                    if key not in {"coach_id", "created_at", "updated_at", "provider", "provider_id"}
                    and value is not None
                }
                if update_values:
                    await self.update(db, existing.coach_id, update_values)
                return existing

        existing = await self.get_by_normalized_name(db, normalized_name, provider)
        if existing is not None:
            update_values = {
                key: value
                for key, value in coach_data.items()
                if key not in {"coach_id", "created_at", "updated_at", "provider", "provider_id"}
                and value is not None
            }
            if update_values:
                await self.update(db, existing.coach_id, update_values)
            return existing

        return await self.create(db, coach_data)

    async def count(self, db: AsyncSession) -> int:
        result = await db.execute(select(Coach.coach_id))
        return len(result.scalars().all())
