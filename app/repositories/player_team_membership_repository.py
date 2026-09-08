from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.player_team_membership import PlayerTeamMembership


class PlayerTeamMembershipRepository:
    """SQL-only persistence for Player-Team membership observations."""

    async def get_identity(
        self,
        db: AsyncSession,
        *,
        player_id: int,
        team_id: int,
        provider: str,
        valid_from: datetime | None,
    ) -> PlayerTeamMembership | None:
        query = select(PlayerTeamMembership).where(
            PlayerTeamMembership.player_id == player_id,
            PlayerTeamMembership.team_id == team_id,
            PlayerTeamMembership.provider == provider,
        )
        if valid_from is None:
            query = query.where(PlayerTeamMembership.valid_from.is_(None))
        else:
            query = query.where(PlayerTeamMembership.valid_from == valid_from)
        result = await db.execute(query)
        return result.scalar_one_or_none()

    async def upsert_current_squad_membership(
        self,
        db: AsyncSession,
        *,
        player_id: int,
        team_id: int,
        provider: str,
        provider_team_id: str,
    ) -> PlayerTeamMembership:
        membership = await self.get_identity(
            db,
            player_id=player_id,
            team_id=team_id,
            provider=provider,
            valid_from=None,
        )
        if membership is None:
            membership = PlayerTeamMembership(
                player_id=player_id,
                team_id=team_id,
                provider=provider,
                provider_team_id=provider_team_id,
                valid_from=None,
                valid_to=None,
                is_current=True,
                source="squad",
            )
            db.add(membership)
        else:
            membership.provider_team_id = provider_team_id
            membership.is_current = True
            membership.source = "squad"
        await db.flush()
        return membership

    async def close_other_current_memberships(
        self,
        db: AsyncSession,
        *,
        player_id: int,
        team_id: int,
        provider: str,
    ) -> None:
        await db.execute(
            update(PlayerTeamMembership)
            .where(
                PlayerTeamMembership.player_id == player_id,
                PlayerTeamMembership.team_id != team_id,
                PlayerTeamMembership.provider == provider,
                PlayerTeamMembership.is_current.is_(True),
            )
            .values(is_current=False, valid_to=func.now(), updated_at=func.now())
        )