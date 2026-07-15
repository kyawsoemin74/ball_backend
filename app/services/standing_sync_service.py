import logging

from sqlalchemy import event
from sqlalchemy.orm import Session
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache import make_cache_key
from app.cache import cache_delete_sync
from app.providers.standing_provider import StandingProvider
from app.repositories.allowed_league_repository import AllowedLeagueRepository
from app.repositories.standing_repository import StandingRepository
from app.services.cache_service import CacheService
from app.services.team_service import TeamService

logger = logging.getLogger(__name__)

_STANDINGS_POST_COMMIT_CACHE_KEYS = "_standings_post_commit_cache_keys"


@event.listens_for(Session, "after_commit")
def _run_standings_post_commit_cache_invalidation(session: Session) -> None:
    keys = session.info.pop(_STANDINGS_POST_COMMIT_CACHE_KEYS, None)
    if not keys:
        return
    for key in keys:
        cache_delete_sync(key)


@event.listens_for(Session, "after_rollback")
def _clear_standings_post_commit_cache_invalidation(session: Session) -> None:
    session.info.pop(_STANDINGS_POST_COMMIT_CACHE_KEYS, None)


class StandingSyncService:
    """Owns standings synchronization/write orchestration."""

    def __init__(
        self,
        standing_provider: StandingProvider,
        team_service: TeamService,
        cache_service: CacheService,
    ) -> None:
        self.standing_provider = standing_provider
        self.team_service = team_service
        self.cache_service = cache_service
        self.standing_repository = StandingRepository()
        self.allowed_league_repository = AllowedLeagueRepository()

    def _flatten_standings_groups(self, api_result: dict) -> list:
        standings_groups = api_result["response"][0].get("league", {}).get("standings", [])
        if isinstance(standings_groups, list) and standings_groups and all(isinstance(item, dict) for item in standings_groups):
            return standings_groups

        flattened = []
        if not isinstance(standings_groups, list):
            raise TypeError("Unexpected standings format: expected a list of groups")

        for group in standings_groups:
            if isinstance(group, list):
                flattened.extend(group)
            else:
                raise TypeError("Unexpected standings group element type: %s" % type(group))

        return flattened

    def _prepare_standings_rows(self, standings_data: list) -> list[dict]:
        prepared_rows = []
        seen_team_ids: set[int] = set()

        for standing in standings_data:
            team = standing.get("team") or {}
            if not isinstance(team, dict):
                logger.warning("Skipping standings row with invalid team payload: %r", standing)
                continue

            team_id = team.get("id")
            if team_id is None:
                logger.warning("Skipping standings row with missing team_id: %r", standing)
                continue

            normalized_team_id = int(team_id)
            if normalized_team_id in seen_team_ids:
                logger.warning("Skipping duplicate standings row for team_id=%s", normalized_team_id)
                continue

            if not team.get("name"):
                logger.warning("Skipping standings row with missing team name for team_id=%s", normalized_team_id)
                continue

            seen_team_ids.add(normalized_team_id)
            prepared_rows.append(standing)

        return prepared_rows

    def _queue_standings_cache_invalidation(self, db: AsyncSession, league_id: int, season: str) -> None:
        key = make_cache_key("standings", league_id, season)
        sync_session = getattr(db, "sync_session", None)
        if sync_session is None:
            # Unit-test fakes may not expose SQLAlchemy session internals.
            self.cache_service.delete_sync(key)
            return

        sync_info = sync_session.info
        keys = sync_info.get(_STANDINGS_POST_COMMIT_CACHE_KEYS)
        if keys is None:
            keys = set()
            sync_info[_STANDINGS_POST_COMMIT_CACHE_KEYS] = keys
        keys.add(key)

    async def upsert_standings(self, db: AsyncSession, standings_data: list, league_id: int, season: str):
        prepared_rows = self._prepare_standings_rows(standings_data)
        team_payload = []
        for standing in prepared_rows:
            team = standing.get("team") or {}
            team_payload.append({"team_id": int(team["id"]), "name": team.get("name"), "logo": team.get("logo"), "country": team.get("country")})

        await self.team_service.ensure_teams_exist(db, team_payload)
        logger.debug("Standings sync ensured %s teams before insert", len(team_payload))

        await db.flush()

        persistence_rows = []
        for standing in prepared_rows:
            goals_for = standing.get("all", {}).get("goals", {}).get("for", 0)
            goals_against = standing.get("all", {}).get("goals", {}).get("against", 0)
            persistence_rows.append(
                {
                    "league_id": league_id,
                    "season": str(season),
                    "team_id": standing["team"]["id"],
                    "position": standing["rank"],
                    "team_name": standing["team"]["name"],
                    "team_logo": standing["team"]["logo"],
                    "group_name": standing.get("group"),
                    "form": standing.get("form"),
                    "description": standing.get("description"),
                    "points": standing["points"],
                    "played": standing["all"]["played"],
                    "won": standing["all"]["win"],
                    "drawn": standing["all"]["draw"],
                    "lost": standing["all"]["lose"],
                    "goals_for": goals_for,
                    "goals_against": goals_against,
                    "goal_difference": standing.get("goalsDiff", goals_for - goals_against),
                }
            )

        await self.standing_repository.upsert_for_league_season(db, league_id, season, persistence_rows)

        await db.flush()
        if not getattr(self, "_defer_standings_cache_invalidation", False):
            self._queue_standings_cache_invalidation(db, league_id, str(season))
        return len(prepared_rows)

    async def sync_standings(self, db: AsyncSession, league_id: int, season: int) -> dict:
        allowed_ids = await self.allowed_league_repository.get_allowed_ids(db)
        if not allowed_ids:
            logger.info("Allowed league list is empty; skipping standings synchronization for league_id=%s", league_id)
            return {"success": True, "league_id": league_id, "season": season, "updated": 0, "message": "League is not allowed for synchronization"}

        if league_id not in allowed_ids:
            logger.debug("SKIPPED LEAGUE: league_id=%s league_name=%s", league_id, "requested league")
            return {"success": True, "league_id": league_id, "season": season, "updated": 0, "message": "League is not allowed for synchronization"}

        logger.debug("ALLOWED LEAGUE: league_id=%s league_name=%s", league_id, "requested league")
        result = await self.standing_provider.get_league_standings(league_id, season)
        if not result or "response" not in result or not result["response"]:
            return {"success": False, "message": "No standings data found from API"}

        try:
            standings_list = self._flatten_standings_groups(result)
            updated_count = await self.upsert_standings(db, standings_list, league_id, str(season))
            if updated_count is None:
                updated_count = len(self._prepare_standings_rows(standings_list))
            return {"success": True, "league_id": league_id, "season": season, "updated": updated_count}
        except (KeyError, IndexError, TypeError) as exc:
            logger.error("Error parsing standings response: %s", exc)
            return {"success": False, "message": "Unexpected API response format"}
