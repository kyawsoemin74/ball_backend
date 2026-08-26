from collections import defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.match_event import MatchEvent
from app.repositories.player_repository import PlayerRepository
from app.services.player_sync_service import PlayerSyncService


class PlayerEventReconciliationService:
    """Reconcile persisted provider event identities without guessing."""

    def __init__(
        self,
        player_repository: PlayerRepository | None = None,
        player_sync_service: PlayerSyncService | None = None,
    ) -> None:
        self.player_repository = player_repository or PlayerRepository()
        self.player_sync_service = player_sync_service or PlayerSyncService(
            player_repository=self.player_repository,
        )

    async def reconcile_assist_ids(self, db: AsyncSession) -> dict[str, Any]:
        result = await db.execute(select(MatchEvent).order_by(MatchEvent.id))
        events = list(result.scalars().all())

        grouped_names: dict[str, set[str]] = defaultdict(set)
        provider_ids_by_event: dict[int, str] = {}
        for event in events:
            provider_id = event.provider_assist_id or (
                str(event.assist_id) if event.assist_id is not None else None
            )
            if provider_id is None:
                continue
            provider_ids_by_event[event.id] = provider_id
            if event.assist_name and event.assist_name.strip():
                grouped_names[provider_id].add(event.assist_name.strip())

        existing_by_provider: dict[str, Any] = {}
        for provider_id in grouped_names:
            player = await self.player_repository.get_by_provider_id(
                db, provider_id, "api-football"
            )
            if player is not None:
                existing_by_provider[provider_id] = player

        created = 0
        resolved = 0
        preserved_unresolved = 0
        conflicting = set()

        for provider_id, names in grouped_names.items():
            if provider_id in existing_by_provider:
                continue
            if len(names) != 1:
                conflicting.add(provider_id)
                continue

            player = await self.player_sync_service.upsert_player(
                db,
                {
                    "provider": "api-football",
                    "provider_id": provider_id,
                    "name": next(iter(names)),
                },
            )
            existing_by_provider[provider_id] = player
            created += 1

        for event in events:
            provider_id = provider_ids_by_event.get(event.id)
            if provider_id is None:
                continue

            event.provider_assist_id = provider_id
            player = existing_by_provider.get(provider_id)
            if player is not None:
                if event.assist_id != player.player_id:
                    event.assist_id = player.player_id
                    resolved += 1
            elif provider_id in conflicting:
                event.assist_id = None
                preserved_unresolved += 1

        await db.flush()
        return {
            "success": True,
            "events_scanned": len(events),
            "masters_created": created,
            "assist_rows_resolved": resolved,
            "assist_rows_preserved_unresolved": preserved_unresolved,
            "conflicting_provider_ids": len(conflicting),
        }

    async def populate_provider_scorer_ids(self, db: AsyncSession) -> int:
        result = await db.execute(
            select(MatchEvent).where(
                MatchEvent.player_id.is_not(None),
                MatchEvent.provider_player_id.is_(None),
            )
        )
        updated = 0
        for event in result.scalars().all():
            player = await self.player_repository.get_by_id(db, event.player_id)
            if player is None:
                continue
            event.provider_player_id = player.provider_id
            updated += 1
        await db.flush()
        return updated