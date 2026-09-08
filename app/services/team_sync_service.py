import logging

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.cache import cache_delete_sync, make_cache_key
from app.services.coach_sync_service import CoachSyncService
from app.repositories.team_repository import TeamRepository
from app.services.cache_service import CacheService
from app.services.country_sync_service import CountrySyncService

logger = logging.getLogger(__name__)

_TEAM_POST_COMMIT_CACHE_KEYS = "_team_post_commit_cache_keys"


@event.listens_for(Session, "after_commit")
def _run_team_post_commit_cache_invalidation(session: Session) -> None:
    keys = session.info.pop(_TEAM_POST_COMMIT_CACHE_KEYS, None)
    if not keys:
        return
    for key in keys:
        cache_delete_sync(key)


@event.listens_for(Session, "after_rollback")
def _clear_team_post_commit_cache_invalidation(session: Session) -> None:
    session.info.pop(_TEAM_POST_COMMIT_CACHE_KEYS, None)


class TeamSyncService:
    """Owns Team synchronization/write orchestration."""

    def __init__(
        self,
        cache_service: CacheService,
        team_repository: TeamRepository | None = None,
        coach_sync_service: CoachSyncService | None = None,
    ) -> None:
        self.cache_service = cache_service
        self.team_repository = team_repository or TeamRepository()
        self.coach_sync_service = coach_sync_service or CoachSyncService()
        self.country_sync_service = CountrySyncService()

    @staticmethod
    def _queue_team_cache_invalidation(db: AsyncSession, team_id: int) -> None:
        key = make_cache_key("team", team_id)
        sync_session = getattr(db, "sync_session", None)
        if sync_session is None:
            # Unit-test fakes may not expose SQLAlchemy session internals.
            cache_delete_sync(key)
            return

        sync_info = sync_session.info
        keys = sync_info.get(_TEAM_POST_COMMIT_CACHE_KEYS)
        if keys is None:
            keys = set()
            sync_info[_TEAM_POST_COMMIT_CACHE_KEYS] = keys
        keys.add(key)

    async def update_team_context(
        self,
        db: AsyncSession,
        team_id: int,
        *,
        current_league_id: int | None = None,
        current_season: str | None = None,
    ) -> None:
        existing_team = await self.team_repository.get_by_id(db, team_id)
        if existing_team is None:
            return

        existing_league_id = getattr(existing_team, "current_league_id", None)
        existing_season = getattr(existing_team, "current_season", None)

        if existing_league_id == current_league_id and existing_season == current_season:
            return

        await self.team_repository.update_team_context(
            db,
            team_id,
            current_league_id=current_league_id,
            current_season=current_season,
        )

    async def resolve_provider_teams(self, db: AsyncSession, teams_data: list[dict]) -> dict:
        """Resolve provider Team IDs to existing local Team Master IDs."""
        if not teams_data:
            return {"resolved": {}, "unresolved": [], "total": 0}

        resolved = {}
        unresolved = []
        for item in teams_data:
            if not isinstance(item, dict):
                unresolved.append(item)
                continue

            provider_id = item.get("provider_id", item.get("id"))
            if provider_id is None:
                unresolved.append(item)
                continue

            team = await self.team_repository.find_by_provider_identity(
                db,
                "api-football",
                provider_id,
            )
            if team is None:
                unresolved.append(item)
                continue

            resolved[int(provider_id)] = int(team.team_id)

        return {"resolved": resolved, "unresolved": unresolved, "total": len(teams_data)}

    async def ensure_teams_exist(self, db: AsyncSession, teams_data: list[dict]) -> dict:
        """Compatibility wrapper that now resolves existing Masters only."""
        result = await self.resolve_provider_teams(db, teams_data)
        return {
            "created": 0,
            "existing": len(result["resolved"]),
            "unresolved": len(result["unresolved"]),
            "total": result["total"],
        }

    async def upsert_team(self, db: AsyncSession, team_data: dict):
        team_payload = team_data.get("team") or team_data
        provider_id = team_payload.get("id")
        team = await self.team_repository.find_by_provider_identity(db, "api-football", provider_id)
        if team is None:
            return None

        venue_payload = team_data.get("venue") or {}
        update_provider_metadata = getattr(self.team_repository, "update_provider_metadata", None)
        if update_provider_metadata is not None:
            await update_provider_metadata(
                db,
                team.team_id,
                name=team_payload.get("name") or getattr(team, "name", None),
                country=team_payload.get("country") or getattr(team, "country", None),
                logo=team_payload.get("logo") or getattr(team, "logo", None),
                stadium=venue_payload.get("name") or getattr(team, "stadium", None),
                founded=team_payload.get("founded") or getattr(team, "founded", None),
            )
            team.name = team_payload.get("name") or team.name
            team.country = team_payload.get("country") or getattr(team, "country", None)
            team.logo = team_payload.get("logo") or getattr(team, "logo", None)
            team.stadium = venue_payload.get("name") or getattr(team, "stadium", None)
            team.founded = team_payload.get("founded") or getattr(team, "founded", None)

        country_result = await self.country_sync_service.sync_from_team_payload(db, team_data)
        country = country_result.get("country") if isinstance(country_result, dict) else None
        if isinstance(country, dict) and country.get("country_id") is not None:
            normalized_country_id = int(country["country_id"])
            if getattr(team, "country_id", None) != normalized_country_id:
                update_country_id = getattr(self.team_repository, "update_country_id", None)
                if update_country_id is not None:
                    await update_country_id(
                        db,
                        team.team_id,
                        normalized_country_id,
                    )
                team.country_id = normalized_country_id

        return team

    async def sync_team_coach(self, db: AsyncSession, team_id: int) -> dict:
        team = await self.team_repository.get_by_id(db, team_id)
        if team is None or getattr(team, "provider_id", None) is None:
            return {"success": True, "team_id": team_id, "coach_id": None, "updated": False, "reason": "unresolved_team"}

        selection_method = getattr(self.coach_sync_service.provider, "get_team_coach_selection", None)
        if selection_method is None:
            return {"success": True, "team_id": team_id, "coach_id": None, "updated": False, "reason": "invalid_selection_contract"}

        selection = await selection_method(int(team.provider_id))
        if not isinstance(selection, dict):
            return {"success": True, "team_id": team_id, "coach_id": None, "updated": False, "reason": "invalid_selection"}

        selection_status = selection.get("status")
        payload = selection.get("coach")
        if selection_status != "VERIFIED":
            if isinstance(payload, dict):
                await self.coach_sync_service.sync_team_coach(db, payload)
            reason = "coach_unavailable" if selection_status == "NO_DATA" else str(selection_status or "invalid_selection").lower()
            return {
                "success": True,
                "team_id": team_id,
                "coach_id": getattr(team, "coach_id", None),
                "updated": False,
                "reason": reason,
            }

        if not isinstance(payload, dict):
            return {"success": True, "team_id": team_id, "coach_id": None, "updated": False, "reason": "coach_unavailable"}

        coach_record = await self.coach_sync_service.sync_team_coach(db, payload)
        coach_id = coach_record.get("coach_id")
        if coach_id is None:
            return {"success": True, "team_id": team_id, "coach_id": None, "updated": False, "reason": "coach_unresolved"}

        if getattr(team, "coach_id", None) != coach_id:
            await self.team_repository.update_current_coach(db, team_id, coach_id)
            self._queue_team_cache_invalidation(db, team_id)

        return {"success": True, "team_id": team_id, "coach_id": coach_id, "updated": True}
