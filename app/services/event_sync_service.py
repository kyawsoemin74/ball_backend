import logging
from typing import Any, Dict

from sqlalchemy.ext.asyncio import AsyncSession

from app.providers.event_provider import EventProvider
from app.repositories.event_repository import EventRepository
from app.repositories.player_repository import PlayerRepository
from app.services.player_identity_resolution_service import PlayerIdentityResolutionService
from app.services.player_sync_service import PlayerSyncService
from app.monitoring import observe_sync

logger = logging.getLogger(__name__)


class EventSyncService:
    """Write/refresh orchestration for event data without owning read or cache logic."""

    def __init__(
        self,
        event_provider: EventProvider | None = None,
        event_repository: EventRepository | None = None,
        player_repository: PlayerRepository | None = None,
        player_sync_service: PlayerSyncService | None = None,
        player_identity_resolution_service: PlayerIdentityResolutionService | None = None,
    ) -> None:
        self.event_provider = event_provider
        self.event_repository = event_repository or EventRepository()
        self.player_repository = player_repository or PlayerRepository()
        self.player_identity_resolution_service = player_identity_resolution_service or PlayerIdentityResolutionService(
            player_repository=self.player_repository,
        )
        self.player_sync_service = player_sync_service or PlayerSyncService(
            player_repository=self.player_repository,
            player_identity_resolution_service=self.player_identity_resolution_service,
        )

    async def _resolve_provider_player(
        self,
        db: AsyncSession,
        player_payload: Dict[str, Any] | None,
    ) -> tuple[int | None, str | None]:
        if not isinstance(player_payload, dict):
            return None, None

        provider_player_id = player_payload.get("id")
        if provider_player_id in (None, ""):
            return None, None

        provider_player_id = str(provider_player_id)
        resolver = getattr(self.player_sync_service, "player_identity_resolution_service", None)
        if resolver is None:
            resolver = self.player_identity_resolution_service
        if resolver is None:
            resolver = PlayerIdentityResolutionService(player_repository=self.player_repository)

        normalized = self.player_sync_service.normalize_event_player({"player": player_payload})
        result = await resolver.resolve_provider_player_identity(
            db,
            "api-football",
            provider_player_id,
            player_data=normalized,
        )

        if result.status in {"RESOLVED_EXISTING", "CREATE_NEW"}:
            if result.local_player_id is not None:
                return int(result.local_player_id), provider_player_id
            if normalized and result.status == "CREATE_NEW":
                if not hasattr(self.player_sync_service.player_repository, "upsert_one"):
                    return None, provider_player_id
                created = await self.player_sync_service.upsert_player(db, normalized)
                return int(created.player_id), provider_player_id
            return None, provider_player_id

        return None, provider_player_id

    async def _resolve_event_identities(
        self, db: AsyncSession, event: Dict[str, Any]
    ) -> tuple[int | None, str | None, int | None, str | None]:
        player_payload = event.get("player") if isinstance(event.get("player"), dict) else None
        assist_payload = event.get("assist") if isinstance(event.get("assist"), dict) else None

        player_id, provider_player_id = await self._resolve_provider_player(db, player_payload)
        assist_id, provider_assist_id = await self._resolve_provider_player(db, assist_payload)
        return player_id, provider_player_id, assist_id, provider_assist_id

    @observe_sync("events")
    async def refresh_match_events(self, db: AsyncSession, match_id: int) -> Dict[str, Any]:
        logger.info("FINAL_EVENT_SYNC_START", extra={"match_id": match_id})

        result = await self.event_provider.get_match_events(match_id)
        if not result or "response" not in result:
            logger.warning("EVENT_SYNC_FAILED", extra={"match_id": match_id, "reason": "api_error"})
            return {"success": False, "message": "API error"}

        api_events = result["response"]
        resolved_events: list[dict] = []
        for event in api_events:
            if not isinstance(event, dict):
                resolved_events.append(event)
                continue

            resolved_player_id, provider_player_id, resolved_assist_id, provider_assist_id = (
                await self._resolve_event_identities(db, event)
            )
            normalized_event = dict(event)
            normalized_event["resolved_player_id"] = resolved_player_id
            normalized_event["player_id"] = resolved_player_id
            normalized_event["provider_player_id"] = provider_player_id
            normalized_event["resolved_assist_id"] = resolved_assist_id
            normalized_event["provider_assist_id"] = provider_assist_id
            resolved_events.append(normalized_event)

        await self.event_repository.replace_match_events(db, match_id, resolved_events)
        await db.flush()

        logger.info("FINAL_EVENT_SYNC_COMPLETE", extra={"match_id": match_id, "count": len(api_events)})
        return {"success": True, "count": len(api_events), "api_events": resolved_events}
