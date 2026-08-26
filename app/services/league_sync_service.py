import logging
from collections.abc import Awaitable, Callable
from typing import Optional

from sqlalchemy import event
from sqlalchemy.orm import Session
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache import cache_delete_sync, make_cache_key
from app.models.league import League
from app.repositories.allowed_league_repository import AllowedLeagueRepository
from app.repositories.league_repository import LeagueRepository
from app.services.cache_service import CacheService
from app.services.country_sync_service import CountrySyncService
from app.services.league_season_sync_service import LeagueSeasonSyncService

logger = logging.getLogger(__name__)

_LEAGUE_POST_COMMIT_CACHE_KEYS = "_league_post_commit_cache_keys"
_LEAGUES_GROUPED_CACHE_KEY = make_cache_key("leagues_grouped")


@event.listens_for(Session, "after_commit")
def _run_league_post_commit_cache_invalidation(session: Session) -> None:
    keys = session.info.pop(_LEAGUE_POST_COMMIT_CACHE_KEYS, None)
    if not keys:
        return
    for key in keys:
        cache_delete_sync(key)


@event.listens_for(Session, "after_rollback")
def _clear_league_post_commit_cache_invalidation(session: Session) -> None:
    session.info.pop(_LEAGUE_POST_COMMIT_CACHE_KEYS, None)


class LeagueSyncService:
    """Owns league synchronization and write orchestration."""

    def __init__(
        self,
        cache_service: CacheService,
        league_repository: LeagueRepository | None = None,
        allowed_league_repository: AllowedLeagueRepository | None = None,
        fetch_all_leagues: Callable[[], Awaitable[Optional[dict]]] | None = None,
    ) -> None:
        self.cache_service = cache_service
        self.league_repository = league_repository or LeagueRepository()
        self.allowed_league_repository = (
            allowed_league_repository or AllowedLeagueRepository()
        )
        self.country_sync_service = CountrySyncService()
        self.league_season_sync_service = LeagueSeasonSyncService()
        self.fetch_all_leagues = fetch_all_leagues

    def _queue_league_cache_invalidation(
        self,
        db: AsyncSession,
        league_id: int,
    ) -> None:
        key = make_cache_key("league", league_id)
        sync_session = getattr(db, "sync_session", None)
        if sync_session is None:
            # Unit-test fakes may not expose SQLAlchemy session internals.
            self.cache_service.delete_sync(key)
            self.cache_service.delete_sync(_LEAGUES_GROUPED_CACHE_KEY)
            return

        sync_info = sync_session.info
        keys = sync_info.get(_LEAGUE_POST_COMMIT_CACHE_KEYS)
        if keys is None:
            keys = set()
            sync_info[_LEAGUE_POST_COMMIT_CACHE_KEYS] = keys
        keys.add(key)
        keys.add(_LEAGUES_GROUPED_CACHE_KEY)

    async def upsert_league(
        self,
        db: AsyncSession,
        league_data: dict,
        allowed_ids: set[int] | None = None,
    ) -> League | None:
        league_payload = league_data.get("league") or league_data
        provider_id = league_payload.get("id")
        if provider_id is None:
            raise ValueError("League payload is missing the id field")

        master = await self.league_repository.find_by_provider_identity(
            db,
            "api-football",
            provider_id,
        )
        if master is None:
            logger.warning(
                "Skipping unresolved provider League: provider_id=%s",
                provider_id,
            )
            return None

        if allowed_ids is not None and (
            not allowed_ids or master.league_id not in allowed_ids
        ):
            logger.debug(
                "SKIPPED LEAGUE: league_id=%s league_name=%s",
                master.league_id,
                league_payload.get("name"),
            )
            return None

        return await self._upsert_league(db, league_data, master=master)

    async def _upsert_league(
        self,
        db: AsyncSession,
        league_data: dict,
        master: League | None = None,
    ) -> League | None:
        league_payload = league_data.get("league") or league_data
        provider_id = league_payload.get("id")
        if provider_id is None:
            raise ValueError("League payload is missing the id field")
        if master is None:
            master = await self.league_repository.find_by_provider_identity(
                db,
                "api-football",
                provider_id,
            )
        if master is None:
            logger.warning(
                "Skipping unresolved provider League: provider_id=%s",
                provider_id,
            )
            return None
        self._queue_league_cache_invalidation(db, master.league_id)
        return master

    async def sync_all_leagues(self, db: AsyncSession) -> dict:
        logger.info("League sync started")
        if self.fetch_all_leagues is None:
            logger.warning(
                "League sync aborted: fetch_all_leagues callable is not configured"
            )
            return {"success": False, "message": "No leagues data found from API"}

        result = await self.fetch_all_leagues()
        if not result or "response" not in result:
            logger.warning("League sync aborted: no response from API-Football")
            return {"success": False, "message": "No leagues data found from API"}

        leagues = result.get("response", [])
        allowed_ids = await self.allowed_league_repository.get_allowed_ids(db)
        if not allowed_ids:
            logger.info(
                "Allowed league list is empty; "
                "skipping all league synchronization."
            )
            return {"success": True, "inserted": 0, "updated": 0, "total": 0}

        filtered_leagues = []
        for league_data in leagues:
            league_payload = league_data.get("league") or league_data
            provider_id = league_payload.get("id")
            if provider_id is None:
                logger.warning("Skipping invalid league payload: %s", league_data)
                continue

            master = await self.league_repository.find_by_provider_identity(
                db,
                "api-football",
                provider_id,
            )
            if master is None:
                logger.warning(
                    "Skipping unresolved provider League: provider_id=%s",
                    provider_id,
                )
                continue

            if master.league_id not in allowed_ids:
                logger.debug(
                    "SKIPPED LEAGUE: league_id=%s league_name=%s",
                    master.league_id,
                    league_payload.get("name"),
                )
                continue

            logger.debug(
                "ALLOWED LEAGUE: league_id=%s league_name=%s",
                master.league_id,
                league_payload.get("name"),
            )
            filtered_leagues.append((league_data, master))

        if not filtered_leagues:
            logger.info(
                "No allowed leagues were present in the API response; "
                "skipping league synchronization."
            )
            return {"success": True, "inserted": 0, "updated": 0, "total": 0}

        league_ids = [master.league_id for _, master in filtered_leagues]
        existing_lookup = {
            league.league_id: league
            for league in await self.league_repository.get_many_by_ids(
                db,
                league_ids,
            )
        }

        inserted = 0
        updated = 0
        season_metrics = {"inserted": 0, "updated": 0, "no_op": 0, "leagues": 0}
        for league_data, master in filtered_leagues:
            upserted = await self.upsert_league(
                db,
                league_data,
                allowed_ids=allowed_ids,
            )
            if upserted is None:
                continue
            if master.league_id in existing_lookup:
                updated += 1
            else:
                inserted += 1
            season_result = await self.league_season_sync_service.sync_league_seasons(
                db,
                league_id=master.league_id,
                seasons=league_data.get("seasons"),
            )
            season_metrics["inserted"] += season_result["inserted"]
            season_metrics["updated"] += season_result["updated"]
            season_metrics["no_op"] += season_result["no_op"]
            season_metrics["leagues"] += 1
        logger.info("League sync completed")
        logger.info("League sync result: inserted=%s, updated=%s", inserted, updated)
        return {
            "success": True,
            "inserted": inserted,
            "updated": updated,
            "total": len(filtered_leagues),
            "season_sync": season_metrics,
        }
