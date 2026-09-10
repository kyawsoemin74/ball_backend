import logging
from datetime import datetime, timezone
from typing import Any, Callable, Dict

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.providers.lineup_provider import LineupProvider
from app.repositories.lineup_repository import LineupRepository
from app.services.analytics_projection_service import AnalyticsProjectionService, log_projection_failure
from app.services.player_identity_resolution_service import PlayerIdentityResolutionService
from app.repositories.match_repository import MatchRepository
from app.repositories.team_repository import TeamRepository
from app.monitoring import observe_sync

logger = logging.getLogger(__name__)

LINEUP_SYNC_BLOCKED_STATUSES = {"FT", "AET", "PEN", "PST", "CANC", "ABD", "AWD", "WO"}

_RESERVED_LOG_EXTRA_RENAMES = {
    "created": "lineup_created",
    "updated": "lineup_updated",
    "message": "lineup_message",
    "filename": "lineup_filename",
    "module": "lineup_module",
    "name": "lineup_name",
    "levelname": "lineup_levelname",
    "pathname": "lineup_pathname",
    "lineno": "lineup_lineno",
    "process": "lineup_process",
    "thread": "lineup_thread",
}


def _safe_lineup_log_extra(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {
        _RESERVED_LOG_EXTRA_RENAMES.get(key, key): value
        for key, value in payload.items()
    }


async def _invalidate_lineup_cache(cache_service: Any | None, cache_key: str | None, metrics: Dict[str, Any]) -> None:
    if cache_service is None or not cache_key:
        return
    try:
        await cache_service.delete(cache_key)
    except Exception:
        metrics["cache_invalidation"] = "CACHE_INVALIDATION_PENDING"
        logger.exception("LINEUP_SYNC_CACHE_INVALIDATION_FAILED", extra={"cache_key": cache_key})


class LineupSyncService:
    """Write/refresh orchestration for lineup data without owning read or cache logic."""

    def __init__(
        self,
        lineup_provider: LineupProvider | None = None,
        lineup_repository: LineupRepository | None = None,
        analytics_projection_service: AnalyticsProjectionService | None = None,
        player_identity_resolution_service: PlayerIdentityResolutionService | None = None,
        team_repository: TeamRepository | None = None,
    ) -> None:
        self.lineup_provider = lineup_provider
        self.lineup_repository = lineup_repository or LineupRepository()
        self.analytics_projection_service = analytics_projection_service or AnalyticsProjectionService()
        self.player_identity_resolution_service = player_identity_resolution_service or PlayerIdentityResolutionService()
        self.match_repository = MatchRepository()
        self.team_repository = team_repository or TeamRepository()

    async def _rollback_then_verify_existing_lineup(self, db: AsyncSession, match_id: int):
        """Crash-proof recovery contract: rollback before any repository verification query.

        This preserves the service-layer transaction ownership and prevents post-flush repository
        reads from running on a poisoned session.
        """
        await db.rollback()
        return await self.lineup_repository.get_by_match_id(db, match_id)

    def _validate_canonical_lineup_payload(self, payload: list[dict] | Any) -> str | None:
        """Reject canonical payloads that are structurally malformed or semantically contradictory.

        This check intentionally remains a service-layer guard before any LINEUP persistence or
        analytics projection handoff. It relies on the contributor's canonical identity data after
        the Match/Team/Player resolver stages are complete.
        """
        if not isinstance(payload, list) or not payload:
            return "INVALID_LINEUP_PAYLOAD"

        for lineup in payload:
            if not isinstance(lineup, dict):
                return "INVALID_LINEUP_PAYLOAD"

            team = lineup.get("team")
            if not isinstance(team, dict):
                return "INVALID_LINEUP_PAYLOAD"

            formation = lineup.get("formation")
            if formation is not None and (not isinstance(formation, str) or not formation.strip()):
                return "INVALID_LINEUP_PAYLOAD"
            if formation is not None and not all(part.isdigit() for part in formation.replace("-", " ").split()):
                # Validator intentionally accepts tactical words only where they parse into the
                # project's observed formation shape. Avoid inventing a new provider contract.
                if "-" not in formation and not formation.replace("-", "").isdigit():
                    return "INVALID_LINEUP_PAYLOAD"

            for section in ("startXI", "substitutes"):
                if not isinstance(lineup.get(section), list):
                    return "INVALID_LINEUP_PAYLOAD"

            start_entries = lineup.get("startXI") or []
            substitute_entries = lineup.get("substitutes") or []

            # Source provider collections should be explicit lists, but we also enforce a
            # consistent canonical player identity shape before persistence.
            start_seen: set[int] = set()
            substitute_seen: set[int] = set()
            for entry in start_entries:
                if not isinstance(entry, dict):
                    return "INVALID_LINEUP_PAYLOAD"
                player = entry.get("player")
                if not isinstance(player, dict):
                    return "INVALID_LINEUP_PAYLOAD"
                local_player_id = player.get("local_player_id") or player.get("player_id")
                if local_player_id is None:
                    return "INVALID_LINEUP_PAYLOAD"
                try:
                    canonical_id = int(local_player_id)
                except (TypeError, ValueError):
                    return "INVALID_LINEUP_PAYLOAD"
                if canonical_id in start_seen:
                    return "INVALID_LINEUP_PAYLOAD"
                start_seen.add(canonical_id)

            for entry in substitute_entries:
                if not isinstance(entry, dict):
                    return "INVALID_LINEUP_PAYLOAD"
                player = entry.get("player")
                if not isinstance(player, dict):
                    return "INVALID_LINEUP_PAYLOAD"
                local_player_id = player.get("local_player_id") or player.get("player_id")
                if local_player_id is None:
                    return "INVALID_LINEUP_PAYLOAD"
                try:
                    canonical_id = int(local_player_id)
                except (TypeError, ValueError):
                    return "INVALID_LINEUP_PAYLOAD"
                if canonical_id in substitute_seen:
                    return "INVALID_LINEUP_PAYLOAD"
                substitute_seen.add(canonical_id)

            overlap = start_seen & substitute_seen
            if overlap:
                return "INVALID_LINEUP_PAYLOAD"

        return None

    @observe_sync("lineup")
    async def sync_lineup(
        self,
        db: AsyncSession,
        match_id: int,
        *,
        validate_lineup: Callable[[Any], bool],
        cache_service: Any | None = None,
        cache_key: str | None = None,
        allow_terminal_status: bool = False,
        invalidate_cache: bool = False,
    ) -> Dict[str, Any]:
        logger.info("LINEUP_SYNC_START", extra={"match_id": match_id})

        try:
            match = await self.lineup_repository.get_match_status(db, match_id)
            status = (match.status or "").upper() if match and match.status else None
            logger.debug("LINEUP_STATUS_GATE", extra={"match_id": match_id, "status": status})

            if status in LINEUP_SYNC_BLOCKED_STATUSES and not allow_terminal_status:
                metrics = {
                    "success": True,
                    "match_id": match_id,
                    "skipped": True,
                    "reason": "status_blocked",
                    "status": status,
                }
                logger.debug("LINEUP_SYNC_SKIPPED_STATUS", extra={"match_id": match_id, "status": status})
                logger.info("LINEUP_SYNC_COMPLETE", extra=_safe_lineup_log_extra(metrics))
                return metrics

            api_res = await self.lineup_provider.get_match_lineup(match_id)
            lineup_data = api_res.get("response") if isinstance(api_res, dict) else None
            logger.debug(
                "LINEUP_SYNC_FETCHED",
                extra={"match_id": match_id, "has_response": lineup_data is not None},
            )

            if not validate_lineup(lineup_data):
                log_projection_failure("lineup", {"match_id": match_id}, "invalid_provider_response")
                metrics = {"success": False, "match_id": match_id, "reason": "lineup_not_available"}
                logger.warning("LINEUP_SYNC_FAILED", extra=_safe_lineup_log_extra(metrics))
                logger.info("LINEUP_SYNC_COMPLETE", extra=_safe_lineup_log_extra(metrics))
                return metrics

            participant_ids = {getattr(match, "home_team_id", None), getattr(match, "away_team_id", None)} - {None}
            if not participant_ids:
                return {
                    "success": False,
                    "match_id": match_id,
                    "reason": "match_identity_missing",
                    "failure_classification": "MATCH_IDENTITY_MISSING",
                }
            canonical_lineup_data = []
            for lineup in lineup_data:
                provider_team_id = (lineup.get("team") or {}).get("id") if isinstance(lineup, dict) else None
                team = await self.team_repository.find_by_provider_identity(db, "api-football", provider_team_id)
                if team is None:
                    return {
                        "success": False,
                        "match_id": match_id,
                        "reason": "team_identity_missing",
                        "failure_classification": "TEAM_IDENTITY_MISSING",
                    }
                local_team_id = int(team.team_id)
                if local_team_id not in participant_ids:
                    return {
                        "success": False,
                        "match_id": match_id,
                        "reason": "TEAM_NOT_MATCH_PARTICIPANT",
                        "failure_classification": "TEAM_NOT_MATCH_PARTICIPANT",
                        "diagnostics": [{"fixture_id": match_id, "provider_team_id": str(provider_team_id), "local_team_id": local_team_id}],
                    }
                canonical_lineup = dict(lineup)
                canonical_lineup["local_team_id"] = local_team_id
                canonical_lineup_data.append(canonical_lineup)
            lineup_data = canonical_lineup_data

            readiness, canonical_lineup_data = await self.player_identity_resolution_service.resolve_lineup(db, lineup_data)
            unresolved = [item.as_dict() for item in readiness if item.status != "READY"]
            if unresolved:
                failure = unresolved[0]
                metrics = {
                    "success": False,
                    "match_id": match_id,
                    "reason": "player_identity_resolution_failed",
                    "failure_classification": failure["status"],
                    "diagnostics": [
                        {
                            **item,
                            "fixture_id": match_id,
                            "provider_team_id": item.get("team_id"),
                            "attempt_count": None,
                        }
                        for item in unresolved
                    ],
                }
                log_projection_failure("lineup", {"match_id": match_id, "diagnostics": metrics["diagnostics"]}, failure["status"])
                logger.warning("LINEUP_SYNC_FAILED", extra=_safe_lineup_log_extra(metrics))
                logger.info("LINEUP_SYNC_COMPLETE", extra=_safe_lineup_log_extra(metrics))
                return metrics
            lineup_data = canonical_lineup_data

            validation_failure = self._validate_canonical_lineup_payload(lineup_data)
            if validation_failure:
                metrics = {
                    "success": False,
                    "match_id": match_id,
                    "reason": "lineup_payload_invalid",
                    "failure_classification": validation_failure,
                    "diagnostics": [{"fixture_id": match_id, "payload_shape": "lineup"}],
                }
                log_projection_failure("lineup", {"match_id": match_id}, validation_failure)
                logger.warning("LINEUP_SYNC_FAILED", extra=_safe_lineup_log_extra(metrics))
                logger.info("LINEUP_SYNC_COMPLETE", extra=_safe_lineup_log_extra(metrics))
                return metrics

            existing = await self.lineup_repository.get_by_match_id(db, match_id)

            if existing:
                await self.lineup_repository.update_one(db, existing, lineup_data)
                try:
                    await db.flush()
                except Exception:
                    await db.rollback()
                    metrics = {"success": False, "match_id": match_id, "reason": "lineup_sync_failed"}
                    logger.warning("LINEUP_SYNC_FAILED", extra=_safe_lineup_log_extra(metrics))
                    logger.info("LINEUP_SYNC_COMPLETE", extra=_safe_lineup_log_extra(metrics))
                    return metrics
                try:
                    projection = await self.analytics_projection_service.project_lineup(db, match_id, lineup_data)
                except AttributeError as exc:
                    if not self.analytics_projection_service.unavailable_for_fake_db(exc):
                        raise
                    projection = None
                if projection is not None and not projection["success"]:
                    raise ValueError(f"Lineup analytics projection rejected: {projection.get('reason', 'reconciliation failure')}")
                metrics = {"success": True, "match_id": match_id, "created": False, "updated": True}
                if projection is not None:
                    metrics["analytics"] = projection
                logger.info("LINEUP_SYNC_UPDATED", extra=_safe_lineup_log_extra(metrics))
                logger.info("LINEUP_SYNC_COMPLETE", extra=_safe_lineup_log_extra(metrics))
                return metrics

            await self.lineup_repository.create_one(db, match_id, lineup_data)
            try:
                await db.flush()
            except IntegrityError:
                existing_after_race = await self._rollback_then_verify_existing_lineup(db, match_id)
                if not existing_after_race:
                    metrics = {"success": False, "match_id": match_id, "reason": "lineup_sync_failed"}
                    logger.warning("LINEUP_SYNC_FAILED", extra=_safe_lineup_log_extra(metrics))
                    logger.info("LINEUP_SYNC_COMPLETE", extra=_safe_lineup_log_extra(metrics))
                    return metrics

                await self.lineup_repository.update_one(db, existing_after_race, lineup_data)
                try:
                    await db.flush()
                except Exception:
                    await db.rollback()
                    metrics = {"success": False, "match_id": match_id, "reason": "lineup_sync_failed"}
                    logger.warning("LINEUP_SYNC_FAILED", extra=_safe_lineup_log_extra(metrics))
                    logger.info("LINEUP_SYNC_COMPLETE", extra=_safe_lineup_log_extra(metrics))
                    return metrics
                try:
                    projection = await self.analytics_projection_service.project_lineup(db, match_id, lineup_data)
                except AttributeError as exc:
                    if not self.analytics_projection_service.unavailable_for_fake_db(exc):
                        raise
                    projection = None
                if projection is not None and not projection["success"]:
                    raise ValueError(f"Lineup analytics projection rejected: {projection.get('reason', 'reconciliation failure')}")
                metrics = {"success": True, "match_id": match_id, "created": False, "updated": True}
                if projection is not None:
                    metrics["analytics"] = projection
                await _invalidate_lineup_cache(cache_service if invalidate_cache else None, cache_key, metrics)
                logger.debug("LINEUP_SYNC_UPDATED", extra=_safe_lineup_log_extra(metrics))
                logger.info("LINEUP_SYNC_COMPLETE", extra=_safe_lineup_log_extra(metrics))
                return metrics
            except Exception:
                await db.rollback()
                metrics = {"success": False, "match_id": match_id, "reason": "lineup_sync_failed"}
                logger.warning("LINEUP_SYNC_FAILED", extra=_safe_lineup_log_extra(metrics))
                logger.info("LINEUP_SYNC_COMPLETE", extra=_safe_lineup_log_extra(metrics))
                return metrics

            try:
                projection = await self.analytics_projection_service.project_lineup(db, match_id, lineup_data)
            except AttributeError as exc:
                if not self.analytics_projection_service.unavailable_for_fake_db(exc):
                    raise
                projection = None
            if projection is not None and not projection["success"]:
                raise ValueError(f"Lineup analytics projection rejected: {projection.get('reason', 'reconciliation failure')}")
            metrics = {"success": True, "match_id": match_id, "created": True, "updated": False}
            if projection is not None:
                metrics["analytics"] = projection
            logger.info("LINEUP_SYNC_CREATED", extra=_safe_lineup_log_extra(metrics))
            logger.info("LINEUP_SYNC_COMPLETE", extra=_safe_lineup_log_extra(metrics))
            return metrics
        except Exception as exc:
            metrics = {"success": False, "match_id": match_id, "reason": "lineup_sync_failed"}
            logger.exception("LINEUP_SYNC_FAILED", extra=_safe_lineup_log_extra({**metrics, "error": str(exc)}))
            logger.info("LINEUP_SYNC_COMPLETE", extra=_safe_lineup_log_extra(metrics))
            return metrics
