from datetime import datetime, timezone
from typing import List

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.match_event import MatchEvent


class EventRepository:
    async def get_by_match_id(self, db: AsyncSession, match_id: int) -> List[MatchEvent]:
        result = await db.execute(
            select(MatchEvent).where(MatchEvent.match_id == match_id).order_by(MatchEvent.time_elapsed, MatchEvent.time_extra)
        )
        return list(result.scalars().all())

    async def delete_by_match_id(self, db: AsyncSession, match_id: int) -> None:
        await db.execute(delete(MatchEvent).where(MatchEvent.match_id == match_id))

    async def replace_match_events(self, db: AsyncSession, match_id: int, events: list[dict]) -> None:
        await self.delete_by_match_id(db, match_id)
        for event in events:
            db.add(MatchEvent(
                match_id=match_id,
                time_elapsed=event.get("time", {}).get("elapsed"),
                time_extra=event.get("time", {}).get("extra"),
                team_id=event.get("team", {}).get("id"),
                team_name=event.get("team", {}).get("name"),
                player_id=event.get("player", {}).get("id"),
                player_name=event.get("player", {}).get("name"),
                assist_id=event.get("assist", {}).get("id"),
                assist_name=event.get("assist", {}).get("name"),
                type=event.get("type"),
                detail=event.get("detail"),
                comments=event.get("comments"),
                updated_at=datetime.now(timezone.utc),
            ))
