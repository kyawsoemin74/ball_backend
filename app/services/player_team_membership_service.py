from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.player_repository import PlayerRepository
from app.repositories.player_team_membership_repository import PlayerTeamMembershipRepository
from app.repositories.team_repository import TeamRepository


class PlayerTeamMembershipService:
    """Write orchestration for authoritative current squad membership."""

    def __init__(
        self,
        player_repository: PlayerRepository | None = None,
        team_repository: TeamRepository | None = None,
        membership_repository: PlayerTeamMembershipRepository | None = None,
    ) -> None:
        self.player_repository = player_repository or PlayerRepository()
        self.team_repository = team_repository or TeamRepository()
        self.membership_repository = membership_repository or PlayerTeamMembershipRepository()

    async def record_current_squad(
        self,
        db: AsyncSession,
        *,
        provider_team_id: int | str,
        provider_player_ids: list[str],
        provider: str = "api-football",
    ) -> dict[str, Any]:
        provider_team_id = str(provider_team_id)
        team = await self.team_repository.find_by_provider_identity(
            db, provider, provider_team_id
        )
        if team is None:
            return {
                "success": False,
                "reason": "team_master_not_found",
                "created": 0,
                "skipped": len(provider_player_ids),
            }

        created = 0
        skipped = 0
        for provider_player_id in provider_player_ids:
            if not provider_player_id:
                skipped += 1
                continue
            player = await self.player_repository.get_by_provider_id(
                db, str(provider_player_id), provider
            )
            if player is None:
                skipped += 1
                continue
            existing = await self.membership_repository.get_identity(
                db,
                player_id=player.player_id,
                team_id=team.team_id,
                provider=provider,
                valid_from=None,
            )
            await self.membership_repository.close_other_current_memberships(
                db,
                player_id=player.player_id,
                team_id=team.team_id,
                provider=provider,
            )
            await self.membership_repository.upsert_current_squad_membership(
                db,
                player_id=player.player_id,
                team_id=team.team_id,
                provider=provider,
                provider_team_id=provider_team_id,
            )
            if existing is None:
                created += 1

        return {
            "success": True,
            "team_id": team.team_id,
            "created": created,
            "skipped": skipped,
        }