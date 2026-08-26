import logging
from typing import Optional, List

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.player import Player
from app.repositories.player_repository import PlayerRepository

logger = logging.getLogger(__name__)


class PlayerService:
    """
    Read-only service for Player access.
    
    Responsibilities:
    - Read player data
    - Cache read if applicable
    - Domain-level read logic
    
    Must NOT:
    - Write player data
    - Call external provider for mutation
    """

    def __init__(
        self,
        player_repository: Optional[PlayerRepository] = None,
    ) -> None:
        self.player_repository = player_repository or PlayerRepository()

    async def get_player(self, db: AsyncSession, player_id: int) -> Optional[Player]:
        """Get player by local player_id."""
        return await self.player_repository.get_by_id(db, player_id)

    async def get_player_by_provider_id(
        self, db: AsyncSession, provider_id: str, provider: str = "api-football"
    ) -> Optional[Player]:
        """Get player by provider identity."""
        return await self.player_repository.get_by_provider_id(db, provider_id, provider)

    async def get_players_by_provider_ids(
        self, db: AsyncSession, provider_ids: List[str], provider: str = "api-football"
    ) -> List[Player]:
        """Get multiple players by provider IDs."""
        return await self.player_repository.get_many_by_provider_ids(db, provider_ids, provider)

    async def get_all_players(self, db: AsyncSession) -> List[Player]:
        """Get all players."""
        return await self.player_repository.get_all(db)

    async def get_player_count(self, db: AsyncSession) -> int:
        """Get total player count."""
        return await self.player_repository.count(db)
