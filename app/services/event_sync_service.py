import logging
from typing import Any, Dict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.match import Match
from app.providers.event_provider import EventProvider
from app.repositories.event_repository import EventRepository
from app.repositories.player_repository import PlayerRepository
from app.repositories.team_repository import TeamRepository
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
        team_repository: TeamRepository | None = None,
    ) -> None:
        self.event_provider = event_provider
        self.event_repository = event_repository or EventRepository()
        self.player_repository = player_repository or PlayerRepository()
        self.team_repository = team_repository or TeamRepository()
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

    @staticmethod
    def _validate_event_response(result: Any) -> tuple[list[dict] | None, str | None]:
        if not isinstance(result, dict):
            return None, "invalid_response"

        errors = result.get("errors")
        if errors not in (None, {}, [], "", False):
            return None, "provider_error"

        events = result.get("response")
        if not isinstance(events, list):
            return None, "invalid_response"
        if not events:
            return None, "empty_response"

        for event in events:
            if not isinstance(event, dict):
                return None, "malformed_event"

            event_time = event.get("time")
            team = event.get("team")
            event_type = event.get("type")
            if not isinstance(event_time, dict) or not isinstance(team, dict):
                return None, "malformed_event"

            elapsed = event_time.get("elapsed")
            team_id = team.get("id")
            if isinstance(elapsed, bool) or not isinstance(elapsed, int):
                return None, "malformed_event"
            if isinstance(team_id, bool) or not isinstance(team_id, int):
                return None, "malformed_event"
            if not isinstance(event_type, str) or not event_type.strip():
                return None, "malformed_event"

            extra = event_time.get("extra")
            if extra is not None and (isinstance(extra, bool) or not isinstance(extra, int)):
                return None, "malformed_event"
            if team.get("name") is not None and not isinstance(team.get("name"), str):
                return None, "malformed_event"

            for identity_key in ("player", "assist"):
                identity = event.get(identity_key)
                if identity is None:
                    continue
                if not isinstance(identity, dict):
                    return None, "malformed_event"
                identity_id = identity.get("id")
                if identity_id is not None and (
                    isinstance(identity_id, bool)
                    or not isinstance(identity_id, (int, str))
                    or (isinstance(identity_id, str) and not identity_id.strip())
                ):
                    return None, "malformed_event"
                if identity.get("name") is not None and not isinstance(identity.get("name"), str):
                    return None, "malformed_event"

            for optional_string in ("detail", "comments"):
                if event.get(optional_string) is not None and not isinstance(event.get(optional_string), str):
                    return None, "malformed_event"

        return events, None

    async def _get_match_team_ids(
        self,
        db: AsyncSession,
        match_id: int,
    ) -> tuple[int | None, int | None] | None:
        result = await db.execute(
            select(Match).where(Match.local_match_id == match_id)
        )
        match = result.scalar_one_or_none()
        if match is None:
            return None

        return match.home_team_id, match.away_team_id

    @observe_sync("events")
    async def refresh_match_events(
        self,
        db: AsyncSession,
        match_id: int,
        provider_fixture_id: int | None = None,
    ) -> Dict[str, Any]:
        logger.info("FINAL_EVENT_SYNC_START", extra={"match_id": match_id})

        provider_fixture_id = match_id if provider_fixture_id is None else provider_fixture_id
        result = await self.event_provider.get_match_events(provider_fixture_id)
        api_events, validation_error = self._validate_event_response(result)
        if validation_error is not None:
            logger.warning(
                "EVENT_SYNC_FAILED",
                extra={"match_id": match_id, "reason": validation_error},
            )
            return {"success": False, "message": validation_error}

        match_team_ids = await self._get_match_team_ids(db, match_id)
        if match_team_ids is None:
            logger.warning(
                "EVENT_SYNC_FAILED",
                extra={"match_id": match_id, "reason": "match_not_found"},
            )
            return {"success": False, "message": "Match not found"}

        home_team_id, away_team_id = match_team_ids
        resolved_events: list[dict] = []
        for event in api_events or []:
            provider_team_id = event["team"]["id"]
            team = await self.team_repository.find_by_provider_identity(
                db,
                "api-football",
                provider_team_id,
            )
            if team is None:
                logger.warning(
                    "TEAM_IDENTITY_MISSING",
                    extra={"match_id": match_id, "provider_team_id": provider_team_id},
                )
                return {"success": False, "message": "TEAM_IDENTITY_MISSING"}

            canonical_team_id = int(team.team_id)
            if canonical_team_id not in (home_team_id, away_team_id):
                logger.warning(
                    "EVENT_TEAM_IDENTITY_MISMATCH",
                    extra={
                        "match_id": match_id,
                        "provider_team_id": provider_team_id,
                        "canonical_team_id": canonical_team_id,
                        "home_team_id": home_team_id,
                        "away_team_id": away_team_id,
                    },
                )
                return {"success": False, "message": "EVENT_TEAM_IDENTITY_MISMATCH"}

            resolved_player_id, provider_player_id, resolved_assist_id, provider_assist_id = (
                await self._resolve_event_identities(db, event)
            )
            normalized_event = dict(event)
            normalized_event["team"] = {
                **event["team"],
                "id": canonical_team_id,
            }
            normalized_event["canonical_team_id"] = canonical_team_id
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
