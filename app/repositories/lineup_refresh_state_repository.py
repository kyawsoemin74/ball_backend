from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.lineup_refresh_state import LineupRefreshState


class LineupRefreshStateRepository:
    async def get_by_match_id(self, db: AsyncSession, match_id: int) -> LineupRefreshState | None:
        result = await db.execute(
            select(LineupRefreshState).where(LineupRefreshState.match_id == match_id)
        )
        return result.scalar_one_or_none()

    async def is_on_cooldown(
        self,
        db: AsyncSession,
        match_id: int,
        cooldown_seconds: int,
        now_utc: datetime | None = None,
    ) -> bool:
        now_utc = now_utc or datetime.now(timezone.utc)
        state = await self.get_by_match_id(db, match_id)
        if not state or not state.last_refreshed_at:
            return False

        return state.last_refreshed_at >= (now_utc - timedelta(seconds=cooldown_seconds))

    async def touch(self, db: AsyncSession, match_id: int, refreshed_at: datetime | None = None) -> None:
        refreshed_at = refreshed_at or datetime.now(timezone.utc)
        state = await self.get_by_match_id(db, match_id)
        if state:
            state.last_refreshed_at = refreshed_at
            state.updated_at = refreshed_at
            await db.flush()
            return

        db.add(
            LineupRefreshState(
                match_id=match_id,
                last_refreshed_at=refreshed_at,
                updated_at=refreshed_at,
            )
        )
        await db.flush()
