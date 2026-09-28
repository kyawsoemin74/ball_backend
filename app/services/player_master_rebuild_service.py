import logging
from collections import defaultdict
from typing import Any

from app.providers.league_provider import LeagueProvider
from app.providers.player_provider import PlayerProvider
from app.providers.team_provider import TeamProvider
from app.repositories.allowed_league_repository import AllowedLeagueRepository
from app.repositories.league_repository import LeagueRepository
from app.repositories.player_repository import PlayerRepository
from app.repositories.team_repository import TeamRepository
from app.services.league_season_sync_service import LeagueSeasonSyncService
from app.services.player_sync_service import PlayerSyncService
from app.services.player_team_membership_service import PlayerTeamMembershipService
from app.services.team_sync_service import TeamSyncService

logger = logging.getLogger(__name__)


class PlayerMasterRebuildService:
    """Resolve the supported scope and rebuild Player Master through production services."""

    def __init__(
        self,
        *,
        league_provider: LeagueProvider,
        team_provider: TeamProvider,
        player_provider: PlayerProvider,
        allowed_league_repository: AllowedLeagueRepository | None = None,
        league_repository: LeagueRepository | None = None,
        league_season_sync_service: LeagueSeasonSyncService | None = None,
        team_repository: TeamRepository | None = None,
        team_sync_service: TeamSyncService | None = None,
        player_sync_service: PlayerSyncService | None = None,
        membership_service: PlayerTeamMembershipService | None = None,
    ) -> None:
        self.league_provider = league_provider
        self.team_provider = team_provider
        self.player_provider = player_provider
        self.allowed_league_repository = allowed_league_repository or AllowedLeagueRepository()
        self.league_repository = league_repository or LeagueRepository()
        self.league_season_sync_service = league_season_sync_service or LeagueSeasonSyncService()
        self.team_repository = team_repository or TeamRepository()
        self.team_sync_service = team_sync_service or TeamSyncService(
            cache_service=None,
            team_repository=self.team_repository,
            team_provider=team_provider,
        )
        self.player_sync_service = player_sync_service or PlayerSyncService(
            player_provider=player_provider,
        )
        self.membership_service = membership_service or PlayerTeamMembershipService(
            player_repository=self.player_sync_service.player_repository,
        )

    @staticmethod
    def _current_season(payload: dict) -> dict | None:
        response = payload.get("response") if isinstance(payload, dict) else None
        item = response[0] if isinstance(response, list) and response else None
        seasons = item.get("seasons") if isinstance(item, dict) else None
        current = [row for row in seasons or [] if isinstance(row, dict) and row.get("current") is True]
        if len(current) != 1:
            return None
        return current[0]

    async def resolve_scope(self, db, *, persist: bool = True) -> dict[str, Any]:
        allowed_ids = await self.allowed_league_repository.get_allowed_ids(db)
        leagues = []
        blocked = []
        for league_id in sorted(allowed_ids):
            league = await self.league_repository.get_by_id(db, league_id)
            if league is None:
                blocked.append({"league_id": league_id, "reason": "local_league_missing"})
                continue

            provider_id = getattr(league, "provider_id", None)
            if provider_id is None:
                lookup = await self.league_provider.get_leagues_by_name(getattr(league, "name", ""))
                response = lookup.get("response") if isinstance(lookup, dict) else None
                exact = [
                    item.get("league") for item in response or []
                    if isinstance(item, dict)
                    and isinstance(item.get("league"), dict)
                    and str(item["league"].get("name", "")).casefold() == str(league.name).casefold()
                    and (
                        not getattr(league, "country", None)
                        or str((item.get("country") or {}).get("name", "")).casefold()
                        == str(league.country).casefold()
                    )
                ]
                ids = {item.get("id") for item in exact if item.get("id") is not None}
                if len(ids) != 1:
                    blocked.append({"league_id": league_id, "reason": "provider_identity_unresolved"})
                    continue
                provider_id = next(iter(ids))
                if persist:
                    league = await self.league_repository.attach_provider_identity(
                        db, league_id, "api-football", provider_id
                    )

            details = await self.league_provider.get_league_details(int(provider_id))
            current = self._current_season(details or {})
            if current is None:
                blocked.append({"league_id": league_id, "provider_id": str(provider_id), "reason": "current_season_unresolved"})
                continue

            season = str(current.get("year"))
            if persist:
                await self.league_season_sync_service.sync_league_seasons(
                    db,
                    league_id=league_id,
                    seasons=(details.get("response") or [{}])[0].get("seasons", []),
                )

            provider_teams = await self.team_provider.get_league_teams(int(provider_id), int(season))
            if provider_teams is None:
                blocked.append({"league_id": league_id, "provider_id": str(provider_id), "season": season, "reason": "team_collection_failed"})
                continue

            resolved_teams = []
            if persist:
                team_result = await self.team_sync_service.ensure_teams_exist(db, provider_teams)
                if team_result.get("unresolved"):
                    blocked.append({"league_id": league_id, "provider_id": str(provider_id), "season": season, "reason": "team_identity_unresolved", "details": team_result})
                    continue
                resolved_map = team_result.get("resolved", {})
                for item in provider_teams:
                    payload = item.get("team") if isinstance(item, dict) and isinstance(item.get("team"), dict) else item
                    provider_team_id = payload.get("id") if isinstance(payload, dict) else None
                    local_team_id = resolved_map.get(int(provider_team_id)) if provider_team_id is not None else None
                    if local_team_id is not None:
                        await self.team_repository.update_team_context(
                            db,
                            local_team_id,
                            current_league_id=league_id,
                            current_season=season,
                        )
                        resolved_teams.append({"team_id": local_team_id, "provider_id": str(provider_team_id)})
            else:
                for item in provider_teams:
                    payload = item.get("team") if isinstance(item, dict) and isinstance(item.get("team"), dict) else item
                    provider_team_id = payload.get("id") if isinstance(payload, dict) else None
                    if provider_team_id is not None:
                        resolved_teams.append({"provider_id": str(provider_team_id)})

            leagues.append({
                "league_id": league_id,
                "provider": "api-football",
                "provider_id": str(provider_id),
                "name": league.name,
                "season": season,
                "current": True,
                "supported": True,
                "teams": resolved_teams,
                "team_count": len(resolved_teams),
            })

        return {
            "provider": "api-football",
            "allowed_league_count": len(allowed_ids),
            "leagues": leagues,
            "blocked": blocked,
            "supported": not blocked and len(leagues) == len(allowed_ids),
            "expected_team_requests": sum(item["team_count"] for item in leagues),
        }

    async def dry_run(self, db) -> dict[str, Any]:
        scope = await self.resolve_scope(db, persist=False)
        scope["mode"] = "dry_run"
        scope["expected_player_requests"] = scope["expected_team_requests"]
        scope["estimated_player_records"] = None
        scope["pagination"] = "provider response metadata; squad endpoint is collected page-by-page when advertised"
        return scope

    async def rebuild(self, db) -> dict[str, Any]:
        scope = await self.resolve_scope(db, persist=True)
        metrics: dict[str, Any] = {
            "scope": scope,
            "provider_records_seen": 0,
            "provider_records_valid": 0,
            "provider_records_invalid": 0,
            "resolved_existing": 0,
            "created_new": 0,
            "ambiguous": 0,
            "conflict": 0,
            "invalid": 0,
            "provider_errors": 0,
            "retry_count": 0,
            "duplicate_prevented": 0,
            "memberships_created": 0,
            "memberships_skipped": 0,
            "memberships_failed": 0,
            "failed_teams": [],
            "unresolved_players": [],
        }
        if not scope["supported"]:
            metrics["success"] = False
            metrics["reason"] = "scope_blocked"
            return metrics

        for league in scope["leagues"]:
            for team in league["teams"]:
                team_provider_id = team["provider_id"]
                try:
                    collected = await self.player_provider.collect_team_squad(int(team_provider_id))
                    if collected.get("provider_request_status") != "SUCCESS":
                        metrics["provider_errors"] += 1
                        metrics["failed_teams"].append({"provider_team_id": team_provider_id, "error": collected.get("error")})
                        continue

                    for raw_player in collected.get("players", []):
                        metrics["provider_records_seen"] += 1
                        normalized = self.player_sync_service.normalize_squad_player(raw_player)
                        if not normalized or not normalized.get("provider_id"):
                            metrics["provider_records_invalid"] += 1
                            metrics["invalid"] += 1
                            continue
                        metrics["provider_records_valid"] += 1
                        resolution = await self.player_sync_service.player_identity_resolution_service.resolve_provider_player_identity(
                            db,
                            normalized["provider"],
                            normalized["provider_id"],
                            player_data=normalized,
                        )
                        status = resolution.status.casefold()
                        if status == "resolved_existing":
                            player = await self.player_sync_service.upsert_player(db, normalized)
                            metrics["resolved_existing"] += 1
                        elif status == "create_new":
                            player = await self.player_sync_service.upsert_player(db, normalized)
                            metrics["created_new"] += 1
                        else:
                            metrics[status] = metrics.get(status, 0) + 1
                            metrics["unresolved_players"].append({"provider_id": normalized["provider_id"], "status": resolution.status, "reason": resolution.reason})
                            continue

                        membership = await self.membership_service.record_current_squad(
                            db,
                            provider_team_id=team_provider_id,
                            provider_player_ids=[str(player.provider_id)],
                        )
                        metrics["memberships_created"] += membership.get("created", 0)
                        metrics["memberships_skipped"] += membership.get("skipped", 0)
                        if not membership.get("success"):
                            metrics["memberships_failed"] += 1
                except Exception as exc:
                    logger.exception("PLAYER_MASTER_REBUILD_TEAM_FAILED provider_team_id=%s", team_provider_id)
                    metrics["provider_errors"] += 1
                    metrics["failed_teams"].append({"provider_team_id": team_provider_id, "error": str(exc)})

        metrics["success"] = not metrics["failed_teams"] and not metrics["unresolved_players"]
        return metrics
