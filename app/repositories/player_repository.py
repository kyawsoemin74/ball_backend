from typing import List, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.models.player import Player


class PlayerRepository:
    """SQL-only repository for Player persistence."""

    async def get_by_id(self, db: AsyncSession, player_id: int) -> Optional[Player]:
        """Get player by local player_id."""
        result = await db.execute(
            select(Player).where(Player.player_id == player_id)
        )
        return result.scalar_one_or_none()

    async def get_by_provider_id(self, db: AsyncSession, provider_id: str, provider: str = "api-football") -> Optional[Player]:
        """Get player by provider identity (provider, provider_id)."""
        result = await db.execute(
            select(Player).where(
                (Player.provider == provider) & (Player.provider_id == provider_id)
            )
        )
        return result.scalar_one_or_none()

    async def get_many_by_provider_ids(self, db: AsyncSession, provider_ids: List[str], provider: str = "api-football") -> List[Player]:
        """Get multiple players by provider IDs."""
        if not provider_ids:
            return []
        result = await db.execute(
            select(Player).where(
                (Player.provider == provider) & (Player.provider_id.in_(provider_ids))
            )
        )
        return result.scalars().all()

    async def get_all(self, db: AsyncSession) -> List[Player]:
        """Get all players."""
        result = await db.execute(select(Player))
        return result.scalars().all()

    async def get_null_provider_candidates(self, db: AsyncSession) -> List[Player]:
        """Get only legacy candidates eligible for evidence-backed reuse."""
        result = await db.execute(
            select(Player)
            .where((Player.provider_id.is_(None)) | (Player.provider_id == ""))
        )
        return result.scalars().all()

    async def create(self, db: AsyncSession, player_data: dict) -> Player:
        """Create a new player."""
        db_player = Player(**player_data)
        db.add(db_player)
        await db.flush()
        return db_player

    async def upsert_one(self, db: AsyncSession, player_data: dict) -> Player:
        """
        Upsert a single player by (provider, provider_id).
        Never overwrite valid existing data with NULL.
        """
        provider = player_data.get("provider", "api-football")
        provider_id = player_data.get("provider_id")

        if not provider_id:
            raise ValueError("provider_id is required for upsert")

        insert_stmt = pg_insert(Player).values(
            {**player_data, "provider": provider, "provider_id": str(provider_id)}
        )
        update_columns = {
            column.name: func.coalesce(
                getattr(insert_stmt.excluded, column.name),
                getattr(Player, column.name),
            )
            for column in Player.__table__.columns
            if column.name not in {"player_id", "provider", "provider_id", "created_at", "updated_at"}
        }
        update_columns["updated_at"] = func.now()
        upsert_stmt = insert_stmt.on_conflict_do_update(
            constraint="uq_players_provider_provider_id",
            set_=update_columns,
        )
        await db.execute(upsert_stmt)

        player = await self.get_by_provider_id(db, str(provider_id), provider)
        if player is None:
            raise RuntimeError(
                f"Player upsert failed for provider={provider} provider_id={provider_id}"
            )
        return player

    async def upsert_many(self, db: AsyncSession, players_data: List[dict]) -> tuple[int, int]:
        """
        Upsert multiple players.
        Returns (created_count, updated_count).
        """
        if not players_data:
            return 0, 0

        created_count = 0
        updated_count = 0

        for player_data in players_data:
            provider = player_data.get("provider", "api-football")
            provider_id = player_data.get("provider_id")
            existing = await self.get_by_provider_id(db, str(provider_id), provider) if provider_id else None
            await self.upsert_one(db, player_data)
            if existing is None:
                created_count += 1
            else:
                updated_count += 1

        return created_count, updated_count

    async def count(self, db: AsyncSession) -> int:
        """Get total player count."""
        result = await db.execute(select(Player).with_only_columns(Player.player_id))
        return len(result.scalars().all())
