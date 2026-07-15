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
        self.allowed_league_repository = allowed_league_repository or AllowedLeagueRepository()
        self.fetch_all_leagues = fetch_all_leagues

    def _queue_league_cache_invalidation(self, db: AsyncSession, league_id: int) -> None:
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

    async def upsert_league(self, db: AsyncSession, league_data: dict, allowed_ids: set[int] | None = None) -> League | None:
        league_payload = league_data.get("league") or league_data
        league_id = league_payload.get("id")
        if league_id is None:
            raise ValueError("League payload is missing the id field")

        if allowed_ids is not None and (not allowed_ids or int(league_id) not in allowed_ids):
            logger.debug("SKIPPED LEAGUE: league_id=%s league_name=%s", league_id, league_payload.get("name"))
            return None

        return await self._upsert_league(db, league_data)

    async def _upsert_league(self, db: AsyncSession, league_data: dict) -> League | None:
        league_payload = league_data.get("league") or league_data
        league_id = league_payload.get("id")
        if league_id is None:
            raise ValueError("League payload is missing the id field")

        country_payload = league_data.get("country")
        if isinstance(country_payload, dict):
            country_name = country_payload.get("name") or country_payload.get("country")
            country_code = country_payload.get("code")
        else:
            country_name = country_payload or league_payload.get("country")
            country_code = None

        season_value = None
        for season in league_data.get("seasons") or []:
            if isinstance(season, dict):
                season_value = season.get("year") or season.get("season")
            else:
                season_value = season
            break

        existing = await self.league_repository.get_by_id(db, int(league_id))
        upsert_row = {
            "league_id": int(league_id),
            "name": league_payload.get("name") if existing is None else league_payload.get("name", existing.name),
            "country": country_name if existing is None else (country_name or existing.country),
            "country_code": country_code if existing is None else (country_code or existing.country_code),
            "logo": league_payload.get("logo") if existing is None else league_payload.get("logo", existing.logo),
            "season": str(season_value) if season_value is not None else (None if existing is None else existing.season),
            "is_featured": bool(league_payload.get("is_featured", False)) if existing is None else (
                bool(league_payload.get("is_featured", existing.is_featured))
                if "is_featured" in league_payload
                else existing.is_featured
            ),
            "display_order": int(league_payload.get("display_order", 999)) if existing is None else (
                int(league_payload.get("display_order", existing.display_order))
                if "display_order" in league_payload
                else existing.display_order
            ),
        }

        upserted = await self.league_repository.upsert_one(db, upsert_row)
        if db is not None:
            await db.flush()
            self._queue_league_cache_invalidation(db, int(league_id))
        return upserted

    async def sync_all_leagues(self, db: AsyncSession) -> dict:
        logger.info("League sync started")
        if self.fetch_all_leagues is None:
            logger.warning("League sync aborted: fetch_all_leagues callable is not configured")
            return {"success": False, "message": "No leagues data found from API"}

        result = await self.fetch_all_leagues()
        if not result or "response" not in result:
            logger.warning("League sync aborted: no response from API-Football")
            return {"success": False, "message": "No leagues data found from API"}

        leagues = result.get("response", [])
        allowed_ids = await self.allowed_league_repository.get_allowed_ids(db)
        if not allowed_ids:
            logger.info("Allowed league list is empty; skipping all league synchronization.")
            return {"success": True, "inserted": 0, "updated": 0, "total": 0}

        filtered_leagues = []
        for league_data in leagues:
            league_payload = league_data.get("league") or league_data
            league_id = league_payload.get("id")
            if league_id is None:
                logger.warning("Skipping invalid league payload: %s", league_data)
                continue

            league_id_int = int(league_id)
            if league_id_int not in allowed_ids:
                logger.debug("SKIPPED LEAGUE: league_id=%s league_name=%s", league_id_int, league_payload.get("name"))
                continue

            logger.debug("ALLOWED LEAGUE: league_id=%s league_name=%s", league_id_int, league_payload.get("name"))
            filtered_leagues.append(league_data)

        if not filtered_leagues:
            logger.info("No allowed leagues were present in the API response; skipping league synchronization.")
            return {"success": True, "inserted": 0, "updated": 0, "total": 0}

        league_ids = [int((league_data.get("league") or league_data).get("id")) for league_data in filtered_leagues]
        existing_lookup = {league.league_id: league for league in await self.league_repository.get_many_by_ids(db, league_ids)}

        inserted = 0
        updated = 0
        for league_data in filtered_leagues:
            league_payload = league_data.get("league") or league_data
            league_id = int(league_payload.get("id"))

            upserted = await self.upsert_league(db, league_data, allowed_ids=allowed_ids)
            if upserted is None:
                continue
            if league_id in existing_lookup:
                updated += 1
            else:
                inserted += 1
        logger.info("League sync completed")
        logger.info("League sync result: inserted=%s, updated=%s", inserted, updated)
        return {"success": True, "inserted": inserted, "updated": updated, "total": len(filtered_leagues)}
