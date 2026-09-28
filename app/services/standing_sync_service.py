import logging

from sqlalchemy import event
from sqlalchemy.orm import Session
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache import make_cache_key
from app.cache import cache_delete_sync
from app.providers.standing_provider import StandingProvider
from app.repositories.allowed_league_repository import AllowedLeagueRepository
from app.repositories.league_repository import LeagueRepository
from app.repositories.league_season_repository import LeagueSeasonRepository
from app.repositories.standing_repository import StandingRepository
from app.services.cache_service import CacheService
from app.services.season_identity import normalize_season
from app.services.team_service import TeamService
from app.services.analytics_projection_service import AnalyticsProjectionService, log_projection_failure
from app.monitoring import observe_sync

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
        analytics_projection_service: AnalyticsProjectionService | None = None,
    ) -> None:
        self.standing_provider = standing_provider
        self.team_service = team_service
        self.cache_service = cache_service
        self.standing_repository = StandingRepository()
        self.allowed_league_repository = AllowedLeagueRepository()
        self.league_repository = LeagueRepository()
        self.league_season_repository = LeagueSeasonRepository()
        self.analytics_projection_service = analytics_projection_service or AnalyticsProjectionService()

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
        if not isinstance(standings_data, list):
            raise TypeError("Unexpected standings format: expected a list")

        prepared_rows = []
        seen_team_ids: set[int] = set()

        for standing in standings_data:
            if not isinstance(standing, dict):
                raise TypeError("Unexpected standing row format")
            team = standing.get("team") or {}
            if not isinstance(team, dict):
                raise TypeError("Standing row team must be an object")

            team_id = team.get("id")
            if team_id is None:
                raise ValueError("Standing row is missing provider Team identity")
            if isinstance(team_id, bool):
                raise ValueError("Standing row has invalid provider Team identity")

            try:
                normalized_team_id = int(team_id)
            except (TypeError, ValueError) as exc:
                raise ValueError("Standing row has invalid provider Team identity") from exc
            if normalized_team_id <= 0:
                raise ValueError("Standing row has invalid provider Team identity")
            if normalized_team_id in seen_team_ids:
                raise ValueError("Duplicate provider Team identity in standings payload")

            if not isinstance(team.get("name"), str) or not team["name"].strip():
                raise ValueError("Standing row is missing Team name")

            all_stats = standing.get("all")
            if not isinstance(all_stats, dict):
                raise ValueError("Standing row is missing aggregate statistics")
            try:
                int(standing["rank"])
                int(standing["points"])
                for key in ("played", "win", "draw", "lose"):
                    int(all_stats[key])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError("Standing row has invalid ranking statistics") from exc
            goals = all_stats.get("goals")
            if goals is not None and not isinstance(goals, dict):
                raise ValueError("Standing goals must be an object")
            if goals:
                for key in ("for", "against"):
                    if goals.get(key) is not None:
                        try:
                            int(goals[key])
                        except (TypeError, ValueError) as exc:
                            raise ValueError("Standing goals are invalid") from exc

            seen_team_ids.add(normalized_team_id)
            prepared_rows.append(standing)

        return prepared_rows

    def _queue_standings_cache_invalidation(self, db: AsyncSession, league_season_id: int) -> None:
        key = make_cache_key("standings", league_season_id)
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

    async def upsert_standings(
        self,
        db: AsyncSession,
        standings_data: list,
        league_id: int,
        season: str,
        league_season_id: int | None = None,
    ):
        season_text = normalize_season(season)
        if league_season_id is None:
            league_season = await self.league_season_repository.get_by_league_and_season(
                db,
                league_id,
                season_text,
            )
            if league_season is None:
                raise ValueError("LeagueSeason identity is unresolved")
            league_season_id = int(league_season.id)

        prepared_rows = self._prepare_standings_rows(standings_data)
        if not prepared_rows:
            raise ValueError("Cannot replace standings with an empty snapshot")
        provider_team_ids = [
            int((standing.get("team") or {})["id"])
            for standing in prepared_rows
        ]
        resolution = await self.team_service.resolve_provider_teams(
            db,
            [{"provider_id": team_id} for team_id in provider_team_ids],
        )
        if resolution["unresolved"]:
            raise ValueError("Unresolved provider Team identity in standings payload")

        resolved_ids = resolution["resolved"]
        local_team_ids = [int(resolved_ids[provider_team_id]) for provider_team_id in provider_team_ids]
        if len(local_team_ids) != len(set(local_team_ids)):
            raise ValueError("Duplicate local Team identity in standings payload")
        logger.debug("Standings resolved %s provider Team identities", len(resolved_ids))

        await db.flush()

        persistence_rows = []
        for standing in prepared_rows:
            goals = standing.get("all", {}).get("goals") or {}
            goals_for = int(goals.get("for") or 0)
            goals_against = int(goals.get("against") or 0)
            persistence_rows.append(
                {
                    "league_season_id": league_season_id,
                    "team_id": int(resolved_ids[int(standing["team"]["id"])]),
                    "position": int(standing["rank"]),
                    "team_name": standing["team"]["name"],
                    "team_logo": standing["team"].get("logo"),
                    "group_name": standing.get("group"),
                    "form": standing.get("form"),
                    "description": standing.get("description"),
                    "points": int(standing["points"]),
                    "played": int(standing["all"]["played"]),
                    "won": int(standing["all"]["win"]),
                    "drawn": int(standing["all"]["draw"]),
                    "lost": int(standing["all"]["lose"]),
                    "goals_for": goals_for,
                    "goals_against": goals_against,
                    "goal_difference": int(standing.get("goalsDiff") or goals_for - goals_against),
                }
            )

        await self.standing_repository.upsert_for_league_season(db, league_season_id, persistence_rows)
        if getattr(db, "sync_session", None) is None:
            projection = None
        else:
            try:
                projection = await self.analytics_projection_service.project_standing(
                    db, league_id, season, standings_data
                )
            except AttributeError as exc:
                if not self.analytics_projection_service.unavailable_for_fake_db(exc):
                    raise
                projection = None
        if projection is not None and not projection["success"]:
            raise ValueError(f"Standing analytics projection rejected: {projection.get('reason', 'reconciliation failure')}")
        self._last_projection = projection

        await db.flush()
        if not getattr(self, "_defer_standings_cache_invalidation", False):
            self._queue_standings_cache_invalidation(db, league_season_id)
        return len(prepared_rows)

    @observe_sync("standing")
    async def sync_standings(self, db: AsyncSession, league_id: int, season: int) -> dict:
        try:
            season_text = normalize_season(season)
        except ValueError as exc:
            return {"success": False, "message": str(exc), "reason": "invalid_season"}

        allowed_ids = await self.allowed_league_repository.get_allowed_ids(db)
        if league_id not in allowed_ids:
            return {
                "success": False,
                "league_id": league_id,
                "season": int(season_text),
                "updated": 0,
                "message": "League is not allowed for standings synchronization",
                "reason": "league_not_allowed",
            }

        league = await self.league_repository.get_by_id(db, league_id, allowed_ids)
        if league is None:
            return {
                "success": False,
                "league_id": league_id,
                "season": int(season_text),
                "updated": 0,
                "message": "Local League identity was not found",
                "reason": "league_identity_unresolved",
            }

        provider = str(getattr(league, "provider", "") or "").strip().casefold()
        provider_id = str(getattr(league, "provider_id", "") or "").strip()
        try:
            provider_league_id = int(provider_id)
        except (TypeError, ValueError):
            provider_league_id = 0
        if provider != "api-football" or provider_league_id <= 0:
            return {
                "success": False,
                "league_id": league_id,
                "season": int(season_text),
                "updated": 0,
                "message": "League provider identity is unresolved",
                "reason": "provider_identity_unresolved",
            }

        league_season = await self.league_season_repository.get_by_league_and_season(
            db,
            league_id,
            season_text,
        )
        if league_season is None:
            return {
                "success": False,
                "league_id": league_id,
                "season": int(season_text),
                "updated": 0,
                "message": "LeagueSeason identity is unresolved",
                "reason": "league_season_unresolved",
            }
        season_provider = str(getattr(league_season, "provider", "") or "").strip().casefold()
        if season_provider and season_provider != provider:
            return {
                "success": False,
                "league_id": league_id,
                "season": int(season_text),
                "updated": 0,
                "message": "LeagueSeason provider identity does not match the League provider",
                "reason": "provider_season_identity_mismatch",
            }

        logger.debug("ALLOWED LEAGUE: league_id=%s season=%s", league_id, season_text)
        result = await self.standing_provider.get_league_standings(provider_league_id, int(season_text))
        if not result or "response" not in result or not result["response"]:
            log_projection_failure("standing", {"league_id": league_id, "season": season_text}, "provider_failure")
            return {
                "success": False,
                "league_id": league_id,
                "season": int(season_text),
                "updated": 0,
                "message": "No standings data found from API",
                "reason": "provider_failure",
            }

        try:
            standings_list = self._flatten_standings_groups(result)
            prepared_rows = self._prepare_standings_rows(standings_list)
            if not prepared_rows:
                return {
                    "success": False,
                    "league_id": league_id,
                    "season": int(season_text),
                    "updated": 0,
                    "message": "Provider returned an empty standings snapshot",
                    "reason": "empty_snapshot",
                }
            updated_count = await self.upsert_standings(
                db,
                standings_list,
                league_id,
                season_text,
                league_season_id=int(league_season.id),
            )
            if updated_count is None:
                updated_count = len(prepared_rows)
            response = {
                "success": True,
                "league_id": league_id,
                "season": int(season_text),
                "updated": updated_count,
            }
            if getattr(self, "_last_projection", None) is not None:
                response["analytics"] = self._last_projection
            return response
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            logger.error("Error parsing standings response: %s", exc)
            return {
                "success": False,
                "league_id": league_id,
                "season": int(season_text),
                "updated": 0,
                "message": "Unexpected API response format",
                "reason": "invalid_provider_payload",
            }
