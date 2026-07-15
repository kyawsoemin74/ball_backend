import logging

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.cache import cache_delete_sync, make_cache_key
from app.repositories.team_repository import TeamRepository
from app.services.cache_service import CacheService

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
    ) -> None:
        self.cache_service = cache_service
        self.team_repository = team_repository or TeamRepository()

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

    async def ensure_teams_exist(self, db: AsyncSession, teams_data: list[dict]) -> dict:
        """Ensure referenced teams exist in the database with one read + bulk insert."""
        if not teams_data:
            return {"created": 0, "existing": 0, "total": 0}

        normalized_teams = []
        seen_team_ids = set()

        for item in teams_data:
            if not isinstance(item, dict):
                logger.warning("Skipping invalid team payload: %r", item)
                continue

            team_id = item.get("team_id", item.get("id"))
            name = item.get("name")

            if team_id is None:
                logger.warning("Skipping team payload without team_id: %r", item)
                continue
            if not name:
                logger.warning("Skipping team payload without name for team_id=%s", team_id)
                continue
            if team_id in seen_team_ids:
                logger.warning("Skipping duplicate team_id=%s in request payload", team_id)
                continue

            seen_team_ids.add(team_id)
            normalized_teams.append(
                {
                    "team_id": int(team_id),
                    "name": str(name),
                    "country": item.get("country"),
                    "logo": item.get("logo"),
                    "stadium": item.get("stadium"),
                    "founded": item.get("founded"),
                }
            )

        if not normalized_teams:
            return {"created": 0, "existing": 0, "total": 0}

        existing_teams = await self.team_repository.get_many_by_ids(db, [t["team_id"] for t in normalized_teams])
        existing_ids = {team.team_id for team in existing_teams}
        missing_teams = [team for team in normalized_teams if team["team_id"] not in existing_ids]

        if missing_teams:
            await self.team_repository.upsert_many(db, missing_teams)
            await db.flush()

        created = len(missing_teams)
        existing = len(normalized_teams) - created
        logger.debug("ensure_teams_exist: created=%s, existing=%s", created, existing)
        return {"created": created, "existing": existing, "total": len(normalized_teams)}

    async def upsert_team(self, db: AsyncSession, team_data: dict):
        team_id = team_data["team"]["id"]
        upsert_row = {
            "team_id": team_id,
            "name": team_data["team"]["name"],
            "country": team_data["team"].get("country"),
            "logo": team_data["team"].get("logo"),
            "stadium": team_data.get("venue", {}).get("name") if team_data.get("venue") else None,
            "founded": team_data["team"].get("founded"),
        }
        team = await self.team_repository.upsert_one(db, upsert_row)
        await db.flush()
        self._queue_team_cache_invalidation(db, team_id)
        return team
