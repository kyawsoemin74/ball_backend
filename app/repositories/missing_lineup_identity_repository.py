from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.missing_lineup_identity import MissingLineupIdentity


class MissingLineupIdentityRepository:
    async def upsert_missing(
        self,
        db: AsyncSession,
        *,
        match_id: int,
        provider_fixture_id: str | None,
        local_team_id: int | None,
        provider_team_id: str,
        provider: str,
        player_name: str | None,
        shirt_number: int | None,
        position: str | None,
        grid: str | None,
        roster_role: str,
        lineup_position: int,
        missing_reason: str,
        raw_identity_state: str | None,
    ) -> MissingLineupIdentity:
        query = select(MissingLineupIdentity).where(
            MissingLineupIdentity.match_id == match_id,
            MissingLineupIdentity.provider_team_id == provider_team_id,
            MissingLineupIdentity.roster_role == roster_role,
            MissingLineupIdentity.lineup_position == lineup_position,
        )
        result = await db.execute(query)
        record = result.scalar_one_or_none()
        now = datetime.now(timezone.utc)
        if record is None:
            record = MissingLineupIdentity(
                match_id=match_id,
                provider_fixture_id=provider_fixture_id,
                local_team_id=local_team_id,
                provider_team_id=provider_team_id,
                provider=provider,
                player_name=player_name,
                shirt_number=shirt_number,
                position=position,
                grid=grid,
                roster_role=roster_role,
                lineup_position=lineup_position,
                missing_reason=missing_reason,
                raw_identity_state=raw_identity_state,
                first_seen=now,
                last_seen=now,
            )
            db.add(record)
        else:
            record.provider_fixture_id = provider_fixture_id
            record.local_team_id = local_team_id
            record.player_name = player_name
            record.shirt_number = shirt_number
            record.position = position
            record.grid = grid
            record.missing_reason = missing_reason
            record.raw_identity_state = raw_identity_state
            record.last_seen = now
            record.observation_count += 1
            record.resolution_status = "MISSING"
            record.retry_eligible = 1
        await db.flush()
        return record

    async def list_by_match(self, db: AsyncSession, match_id: int) -> list[MissingLineupIdentity]:
        result = await db.execute(
            select(MissingLineupIdentity)
            .where(MissingLineupIdentity.match_id == match_id)
            .order_by(MissingLineupIdentity.lineup_position)
        )
        return list(result.scalars().all())
