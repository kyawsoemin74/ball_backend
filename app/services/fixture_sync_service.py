import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache import make_cache_key
from app.db import async_session
from app.models.match import Match
from app.providers.fixture_provider import FixtureProvider
from app.repositories.allowed_league_repository import AllowedLeagueRepository
from app.repositories.league_repository import LeagueRepository
from app.repositories.match_repository import MatchRepository
from app.repositories.final_lineup_finalization_repository import FinalLineupFinalizationRepository
from app.repositories.final_lineup_finalization_repository import FINAL_LINEUP_MAX_ATTEMPTS
from app.schemas.match import MatchCreate
from app.services.active_match_service import active_match_service
from app.services.base.football_client import FootballAPIClient
from app.services.cache_service import CacheService
from app.services.league_season_sync_service import LeagueSeasonSyncService
from app.services.standing_service import StandingService
from app.services.team_service import TeamService
from app.services.team_sync_service import TeamSyncService
from app.services.venue_sync_service import VenueSyncService
from app.services.referee_sync_service import RefereeSyncService
from app.services.resource_lock import run_with_resource_lock
from app.monitoring import FINALIZATION_TOTAL, observe_sync

logger = logging.getLogger(__name__)

FINISHED_STATUSES = {"FT", "AET", "PEN", "CANC", "ABD", "AWD", "WO"}
LIVE_STATUSES = {"1H", "2H", "HT", "ET", "LIVE", "BT", "P"}
ACTIVE_MATCH_REGISTER_STATUSES = {"1H", "2H", "HT", "LIVE"}
ACTIVE_MATCH_TERMINAL_STATUSES = FINISHED_STATUSES | {"PST"}
FINALIZATION_TERMINAL_STATUSES = ACTIVE_MATCH_TERMINAL_STATUSES
FINAL_LINEUP_TERMINAL_STATUSES = {"FT", "AET", "PEN"}
NON_TERMINAL_STATUSES = {"NS", "TBD", "1H", "2H", "HT", "ET", "LIVE", "BT", "P"}


class FixtureSyncService:
    def __init__(
        self,
        client: FootballAPIClient,
        team_service: TeamService,
        cache_service: CacheService | None = None,
        standing_service: StandingService | None = None,
        fixture_provider: FixtureProvider | None = None,
        referee_sync_service: RefereeSyncService | None = None,
    ) -> None:
        self.client = client
        self.fixture_provider = fixture_provider or FixtureProvider(self.client)
        self.team_service = team_service
        self.cache_service = cache_service or CacheService()
        self.match_repository = MatchRepository()
        self.final_lineup_finalization_repository = FinalLineupFinalizationRepository()
        self.league_repository = LeagueRepository()
        self.allowed_league_repository = AllowedLeagueRepository()
        self.standing_service = standing_service or StandingService(self.client, self.team_service, self.cache_service)
        self.team_sync_service = getattr(team_service, "team_sync_service", None) or TeamSyncService(self.cache_service)
        self.venue_sync_service = VenueSyncService()
        self.referee_sync_service = referee_sync_service or RefereeSyncService()
        self.league_season_sync_service = LeagueSeasonSyncService()

    @staticmethod
    async def _begin_fixture_savepoint(db):
        if not hasattr(db, "begin_nested"):
            return None
        savepoint = db.begin_nested()
        if hasattr(savepoint, "start"):
            await savepoint.start()
        else:
            await savepoint.__aenter__()
        return savepoint

    @staticmethod
    async def _release_fixture_savepoint(savepoint) -> None:
        if savepoint is None:
            return
        if hasattr(savepoint, "commit"):
            await savepoint.commit()
        else:
            await savepoint.__aexit__(None, None, None)

    @staticmethod
    async def _rollback_fixture_savepoint(savepoint, exc: Exception) -> None:
        if savepoint is None:
            return
        if hasattr(savepoint, "rollback"):
            await savepoint.rollback()
        else:
            await savepoint.__aexit__(type(exc), exc, exc.__traceback__)

    def _extract_id_from_logo(self, obj: dict) -> Optional[int]:
        url = obj.get("logo")
        if not url or not isinstance(url, str):
            return None
        match = re.search(r"/teams/(\d+)", url)
        if match:
            try:
                return int(match.group(1))
            except (ValueError, TypeError):
                return None
        return None

    @staticmethod
    def _coerce_season(value: object) -> Optional[int]:
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _collect_standings_prewarm_candidates(self, matches: list[MatchCreate]) -> set[tuple[int, int]]:
        candidates: set[tuple[int, int]] = set()
        for match in matches:
            if match.season is None:
                continue
            try:
                candidates.add((int(match.league_id), int(match.season)))
            except (TypeError, ValueError):
                continue
        return candidates

    async def _sync_active_match_registration(self, match_id: int, status: str | None) -> None:
        try:
            normalized_status = str(status or "").upper()
            if normalized_status in ACTIVE_MATCH_REGISTER_STATUSES:
                await active_match_service.mark_match_active(match_id)
                return

            if normalized_status in ACTIVE_MATCH_TERMINAL_STATUSES:
                await active_match_service.remove_match_active(match_id)
                return

            await active_match_service.remove_match_active(match_id)
        except Exception as exc:
            logger.warning("ACTIVE_MATCH_SYNC_SIDE_EFFECT_FAILED match_id=%s status=%s error=%s", match_id, status, exc)

    async def apply_active_match_updates(self, updates: dict[int, str | None] | None) -> None:
        for match_id, status in (updates or {}).items():
            await self._sync_active_match_registration(int(match_id), status)

    async def _finalize_terminal_match_events(self, db: AsyncSession, match_id: int, status: str | None) -> bool:
        normalized_status = str(status or "").upper()
        if normalized_status not in FINALIZATION_TERMINAL_STATUSES:
            return False

        match_row = await db.execute(select(Match).where(Match.match_id == match_id))
        if match_row is None or not hasattr(match_row, "scalar_one_or_none"):
            return True

        current_match = match_row.scalar_one_or_none()
        if current_match is None:
            return True

        current_state = str(getattr(current_match, "status", "") or "").upper()
        if current_state in {"FINALIZED"}:
            logger.info("FINAL_EVENT_SYNC_SKIPPED match_id=%s reason=already_finalized", match_id)
            return False

        try:
            from app.services.football import football_service

            resource_locked, result = await run_with_resource_lock(
                db,
                "events",
                match_id,
                lambda: football_service.sync_match_events(db, match_id),
            )
            if not resource_locked:
                logger.info("FINAL_EVENT_SYNC_SKIPPED match_id=%s reason=resource_lock_not_acquired", match_id)
                return False
            if not result.get("success"):
                logger.warning(
                    "FINAL_EVENT_SYNC_FAILED match_id=%s status=%s reason=%s",
                    match_id,
                    normalized_status,
                    result.get("message", "event_sync_failed"),
                )
                return False

            return True
        except Exception as exc:
            logger.warning("FINAL_EVENT_SYNC_FAILED match_id=%s status=%s error=%s", match_id, normalized_status, exc)
            return False

    async def _prewarm_missing_standings(self, db: AsyncSession, candidates: set[tuple[int, int]]) -> dict[str, int]:
        total_pairs = len(candidates)
        already_present_pairs = 0
        synced_pairs = 0
        failed_pairs = 0

        logger.info("Daily fixture standings prewarm starting: total_unique_pairs=%s", total_pairs)

        for league_id, season in sorted(candidates):
            logger.info("PREWARM_CANDIDATE league_id=%s season=%s", league_id, season)

            async def sync_prewarm() -> str:
                existing_rows = await self.standing_service.standing_repository.get_for_league_season(
                    db, league_id, season
                )
                if existing_rows:
                    logger.info(
                        "PREWARM_SKIPPED league_id=%s season=%s rows=%s",
                        league_id,
                        season,
                        len(existing_rows),
                    )
                    return "skipped"

                result = await self.standing_service.sync_standings(db, league_id, season)
                if not result.get("success"):
                    logger.warning(
                        "PREWARM_FAILED league_id=%s season=%s reason=%s",
                        league_id,
                        season,
                        result.get("message", "unknown error"),
                    )
                    return "failed"

                logger.info(
                    "PREWARM_SYNCED league_id=%s season=%s updated=%s",
                    league_id,
                    season,
                    result.get("updated", 0),
                )
                return "synced"

            try:
                locked, outcome = await run_with_resource_lock(
                    db,
                    "standing",
                    f"{league_id}:{season}",
                    sync_prewarm,
                )
                if not locked:
                    failed_pairs += 1
                    logger.info(
                        "PREWARM_SKIPPED league_id=%s season=%s reason=lock_not_acquired",
                        league_id,
                        season,
                    )
                elif outcome == "skipped":
                    already_present_pairs += 1
                elif outcome == "synced":
                    synced_pairs += 1
                else:
                    failed_pairs += 1
            except Exception as exc:
                failed_pairs += 1
                logger.warning(
                    "PREWARM_FAILED league_id=%s season=%s error=%s",
                    league_id,
                    season,
                    exc,
                )

        logger.info(
            "Daily fixture standings prewarm completed: total_unique_pairs=%s already_present_pairs=%s synced_pairs=%s failed_pairs=%s",
            total_pairs,
            already_present_pairs,
            synced_pairs,
            failed_pairs,
        )
        return {
            "total_unique_pairs": total_pairs,
            "already_present_pairs": already_present_pairs,
            "synced_pairs": synced_pairs,
            "failed_pairs": failed_pairs,
        }

    def parse_fixture_to_match(self, fixture: dict, league_id: int | None = None) -> Optional[MatchCreate]:
        try:
            f_info = fixture.get("fixture", {})
            f_league = fixture.get("league", {})
            f_teams = fixture.get("teams", {})
            f_goals = fixture.get("goals", {})
            date_str = f_info.get("date")
            if not date_str:
                return None

            dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            home_team_id = f_teams.get("home", {}).get("id") or self._extract_id_from_logo(f_teams.get("home", {}))
            away_team_id = f_teams.get("away", {}).get("id") or self._extract_id_from_logo(f_teams.get("away", {}))
            resolved_league_id = league_id if league_id is not None else f_league.get("id")
            if resolved_league_id is None:
                return None
            return MatchCreate(
                match_id=int(f_info.get("id")),
                league_id=resolved_league_id,
                season=self._coerce_season(f_league.get("season")),
                league_name=f_league.get("name"),
                league_logo=f_league.get("logo"),
                country_name=f_league.get("country"),
                country_logo=f_league.get("flag"),
                match_time=dt,
                status=f_info.get("status", {}).get("short", "NS"),
                elapsed=f_info.get("status", {}).get("elapsed", 0) or 0,
                home_team=f_teams.get("home", {}).get("name"),
                home_team_id=home_team_id,
                home_team_logo=f_teams.get("home", {}).get("logo"),
                away_team=f_teams.get("away", {}).get("name"),
                away_team_id=away_team_id,
                away_team_logo=f_teams.get("away", {}).get("logo"),
                home_score=f_goals.get("home") or 0,
                away_score=f_goals.get("away") or 0,
                venue_name=f_info.get("venue", {}).get("name", "Unknown"),
                venue_city=f_info.get("venue", {}).get("city", "Unknown"),
            )
        except Exception as exc:
            logger.error("Parsing error for Fixture ID %s: %s", fixture.get("fixture", {}).get("id"), exc)
            return None

    async def _process_sync_with_candidates(self, db: AsyncSession, fixtures: list) -> tuple[dict, set[tuple[int, int]]]:
        allowed_ids = await self.allowed_league_repository.get_allowed_ids(db)
        if not allowed_ids:
            logger.info("Allowed league list is empty; skipping all fixture synchronization.")
            return {"success": True, "inserted": 0, "updated": 0, "total": 0, "failed": 0, "final_lineup_candidates": []}, set()

        filtered_fixtures = []
        for fixture_raw in fixtures:
            league_info = fixture_raw.get("league") or {}
            provider_id = league_info.get("id")
            league_name = league_info.get("name") or "Unknown league"
            if provider_id is None:
                logger.warning("Skipping fixture %s with missing league_id", fixture_raw.get("fixture", {}).get("id"))
                continue

            master = await self.league_repository.find_by_provider_identity(db, "api-football", provider_id)
            if master is None:
                logger.warning("Skipping fixture with unresolved provider League: provider_id=%s", provider_id)
                continue

            if master.league_id not in allowed_ids:
                logger.debug("SKIPPED LEAGUE: league_id=%s league_name=%s", master.league_id, league_name)
                continue

            logger.debug("ALLOWED LEAGUE: league_id=%s league_name=%s", master.league_id, league_name)
            filtered_fixtures.append((fixture_raw, master.league_id))

        if not filtered_fixtures:
            logger.info("No allowed leagues were present in the fixture payload; skipping fixture synchronization.")
            return {"success": True, "inserted": 0, "updated": 0, "total": 0, "failed": 0, "final_lineup_candidates": []}, set()

        result = {
            "success": True,
            "inserted": 0,
            "updated": 0,
            "total": len(filtered_fixtures),
            "failed": 0,
            "standings_prewarm_candidates": 0,
            "final_lineup_candidates": [],
            "active_match_updates": {},
        }
        prewarm_candidates: set[tuple[int, int]] = set()
        for fixture_raw, master_league_id in filtered_fixtures:
            fixture_id = fixture_raw.get("fixture", {}).get("id")
            fixture_savepoint = await self._begin_fixture_savepoint(db)
            try:
                match = self.parse_fixture_to_match(fixture_raw, league_id=master_league_id)
                if match is None:
                    result["failed"] += 1
                    logger.warning("Fixture ID %s failed parsing.", fixture_id)
                    await self._release_fixture_savepoint(fixture_savepoint)
                    continue

                if match.season is not None and getattr(db, "sync_session", None) is not None:
                    await self.league_season_sync_service.upsert_season(
                        db,
                        league_id=int(match.league_id),
                        season=int(match.season),
                        provider="api-football",
                    )

                referee_id = None
                referee_source = (fixture_raw.get("fixture") or {}).get("referee")
                if isinstance(referee_source, str) and referee_source.strip():
                    referee_result = await self.referee_sync_service.sync_fixture_referee(
                        db,
                        referee_source,
                    )
                    referee_id = referee_result.get("referee_id")
                    logger.debug(
                        "REFEREE_RESOLVED",
                        extra={"fixture_id": fixture_id, "referee_id": referee_id},
                    )

                venue_result = await self.venue_sync_service.sync_fixture_payload(
                    db,
                    (fixture_raw.get("fixture") or {}).get("venue"),
                )
                venue_id = venue_result.get("venue_id")
                if venue_id is not None:
                    logger.debug("VENUE_RESOLVED", extra={"fixture_id": fixture_id, "venue_id": venue_id})

                if match.season is not None:
                    try:
                        prewarm_candidates.add((int(match.league_id), int(match.season)))
                    except (TypeError, ValueError):
                        pass

                provider_team_ids = [
                    team_id
                    for team_id in (match.home_team_id, match.away_team_id)
                    if team_id is not None
                ]
                resolution = await self.team_service.resolve_provider_teams(
                    db,
                    [{"provider_id": team_id} for team_id in provider_team_ids],
                )
                if resolution["unresolved"]:
                    result["failed"] += 1
                    logger.warning("Fixture ID %s has unresolved provider Team identity", fixture_id)
                    await self._release_fixture_savepoint(fixture_savepoint)
                    continue

                resolved_ids = resolution["resolved"]
                if hasattr(self.team_sync_service, "sync_team_coach"):
                    for provider_team_id, local_team_id in resolved_ids.items():
                        await self.team_sync_service.sync_team_coach(db, int(local_team_id))
                if match.home_team_id is not None:
                    match.home_team_id = resolved_ids[int(match.home_team_id)]
                if match.away_team_id is not None:
                    match.away_team_id = resolved_ids[int(match.away_team_id)]

                existing_rows = await self.match_repository.get_many_by_ids(db, [match.match_id])
                existing_ids = {row.match_id for row in existing_rows}
                previous_status = None
                if existing_rows:
                    previous_status = str(getattr(existing_rows[0], "status", "") or "").upper() or None
                updated = 1 if match.match_id in existing_ids else 0
                inserted = 1 - updated

                match_row = match.model_dump()
                match_row["venue_id"] = venue_id
                match_row["referee_id"] = referee_id
                insert_stmt = pg_insert(Match).values([match_row])
                match_update_fields = {
                    "league_id": insert_stmt.excluded.league_id,
                    "season": insert_stmt.excluded.season,
                    "league_name": insert_stmt.excluded.league_name,
                    "league_logo": insert_stmt.excluded.league_logo,
                    "country_name": insert_stmt.excluded.country_name,
                    "country_logo": insert_stmt.excluded.country_logo,
                    "match_time": insert_stmt.excluded.match_time,
                    "status": insert_stmt.excluded.status,
                    "elapsed": insert_stmt.excluded.elapsed,
                    "home_team": insert_stmt.excluded.home_team,
                    "home_team_id": insert_stmt.excluded.home_team_id,
                    "home_team_logo": insert_stmt.excluded.home_team_logo,
                    "away_team": insert_stmt.excluded.away_team,
                    "away_team_id": insert_stmt.excluded.away_team_id,
                    "away_team_logo": insert_stmt.excluded.away_team_logo,
                    "home_score": insert_stmt.excluded.home_score,
                    "away_score": insert_stmt.excluded.away_score,
                    "venue_name": insert_stmt.excluded.venue_name,
                    "venue_city": insert_stmt.excluded.venue_city,
                    "referee_id": insert_stmt.excluded.referee_id,
                }
                if venue_id is not None:
                    match_update_fields["venue_id"] = insert_stmt.excluded.venue_id
                upsert_stmt = insert_stmt.on_conflict_do_update(
                    index_elements=["fixture_id"],
                    set_=match_update_fields,
                )
                await db.execute(upsert_stmt)

                home_team_id = getattr(match, "home_team_id", None)
                away_team_id = getattr(match, "away_team_id", None)
                current_league_id = int(match.league_id) if match.league_id is not None else None
                current_season = str(match.season) if match.season is not None else None

                if current_league_id is not None and current_season is not None:
                    if home_team_id is not None:
                        await self.team_sync_service.update_team_context(
                            db,
                            int(home_team_id),
                            current_league_id=current_league_id,
                            current_season=current_season,
                        )

                    if away_team_id is not None:
                        await self.team_sync_service.update_team_context(
                            db,
                            int(away_team_id),
                            current_league_id=current_league_id,
                            current_season=current_season,
                        )

                await db.flush()

                normalized_status = str(match.status or "").upper()
                finalization_completed = False
                if normalized_status in FINALIZATION_TERMINAL_STATUSES:
                    finalization_completed = await self._finalize_terminal_match_events(db, match.match_id, match.status)

                if (
                    finalization_completed
                    and previous_status in NON_TERMINAL_STATUSES
                    and normalized_status in FINAL_LINEUP_TERMINAL_STATUSES
                ):
                    finalization = await self.final_lineup_finalization_repository.create_required(
                        db,
                        match.match_id,
                    )
                    if finalization.status != "SUCCESS":
                        logger.info(
                            "FINAL_LINEUP_REQUIRED match_id=%s previous_status=%s new_status=%s",
                            match.match_id,
                            previous_status,
                            normalized_status,
                        )
                        result["final_lineup_candidates"].append(match.match_id)

                if finalization_completed or normalized_status not in FINALIZATION_TERMINAL_STATUSES:
                    if getattr(self, "_defer_live_cache_invalidation", False):
                        result["active_match_updates"][match.match_id] = match.status
                    else:
                        await self._sync_active_match_registration(match.match_id, match.status)

                result["inserted"] += inserted
                result["updated"] += updated
                await self._release_fixture_savepoint(fixture_savepoint)
            except Exception as exc:
                await self._rollback_fixture_savepoint(fixture_savepoint, exc)
                result["failed"] += 1
                logger.warning("Fixture ID %s failed during sync: %s", fixture_id, exc)
                continue

        result["standings_prewarm_candidates"] = len(prewarm_candidates)
        try:
            retry_candidates = await self.final_lineup_finalization_repository.get_retry_candidates(db, 100)
            result["final_lineup_candidates"].extend(record.match_id for record in retry_candidates)
            result["final_lineup_candidates"] = sorted(set(result["final_lineup_candidates"]))
        except Exception:
            logger.exception("FINAL_LINEUP_RETRY_DISCOVERY_FAILED")
        return result, prewarm_candidates

    async def finalize_pending_lineups(self, match_ids: list[int] | None = None) -> dict[str, int]:
        candidate_ids = sorted({int(match_id) for match_id in (match_ids or [])})
        metrics = {"attempted": 0, "succeeded": 0, "failed": 0, "skipped": 0}

        if not candidate_ids:
            try:
                async with async_session() as db:
                    retry_candidates = await self.final_lineup_finalization_repository.get_retry_candidates(db, 100)
                candidate_ids = sorted({int(record.match_id) for record in retry_candidates})
            except Exception:
                logger.exception("FINAL_LINEUP_RETRY_DISCOVERY_FAILED")
                return metrics

        for match_id in candidate_ids:
            async with async_session() as db:
                locked, outcome = await run_with_resource_lock(
                    db,
                    "lineup",
                    match_id,
                    lambda: self._run_final_lineup_attempt(db, match_id),
                )

            if not locked:
                metrics["skipped"] += 1
                FINALIZATION_TOTAL.labels("skipped", "lock_conflict").inc()
                logger.info("FINAL_LINEUP_SKIPPED match_id=%s reason=lock_not_acquired", match_id)
                continue

            state = outcome.get("state") if isinstance(outcome, dict) else None
            if state == "success":
                metrics["attempted"] += 1
                metrics["succeeded"] += 1
                FINALIZATION_TOTAL.labels("success", "none").inc()
            elif state == "skipped":
                metrics["skipped"] += 1
                FINALIZATION_TOTAL.labels("skipped", "already_complete").inc()
            else:
                metrics["attempted"] += 1
                metrics["failed"] += 1
                FINALIZATION_TOTAL.labels(
                    "failure",
                    str(outcome.get("failure_category", "DB_FAILURE"))[:64] if isinstance(outcome, dict) else "DB_FAILURE",
                ).inc()
                try:
                    commit_confirmed = await self._persist_final_lineup_failure(
                        match_id,
                        outcome.get("failure_category", "DB_FAILURE"),
                        outcome.get("failure_reason", "final lineup sync failed"),
                        outcome.get("attempted_at"),
                        outcome.get("provider_attempted", False),
                        outcome.get("diagnostics"),
                        verify_commit=state == "commit_unknown",
                    )
                    if commit_confirmed:
                        metrics["failed"] -= 1
                        metrics["succeeded"] += 1
                except Exception:
                    logger.exception("FINAL_LINEUP_FAILURE_METADATA_FAILED match_id=%s", match_id)

        return metrics

    async def _run_final_lineup_attempt(self, db: AsyncSession, match_id: int) -> dict:
        record = await self.final_lineup_finalization_repository.get_by_match_id(db, match_id)
        if record is None or record.status == "SUCCESS":
            return {"state": "skipped"}
        if record.status not in {"REQUIRED", "RETRYABLE"}:
            return {"state": "skipped"}

        attempted_at = datetime.now(timezone.utc)
        provider_attempted = False
        commit_started = False
        try:
            await self.final_lineup_finalization_repository.mark_attempt_started(db, record, attempted_at)
            provider_attempted = True
            if record.status == "RETRYABLE":
                logger.info("FINAL_LINEUP_RETRY match_id=%s", match_id)
            logger.info("FINAL_LINEUP_ATTEMPT match_id=%s", match_id)
            from app.services.football import football_service

            result = await football_service.sync_match_lineup(
                db,
                match_id,
                allow_terminal_status=True,
                invalidate_cache=False,
            )
            if not result.get("success"):
                await db.rollback()
                return {
                    "state": "failure",
                    "failure_category": self._final_lineup_failure_category(result),
                    "failure_reason": result.get("reason", "final lineup sync failed"),
                    "diagnostics": result.get("diagnostics", []),
                    "attempted_at": attempted_at,
                    "provider_attempted": provider_attempted,
                }

            completed_at = datetime.now(timezone.utc)
            await self.final_lineup_finalization_repository.mark_success(db, record, completed_at)
            commit_started = True
            await db.commit()
            try:
                await self.cache_service.delete(make_cache_key("lineup", match_id))
            except Exception:
                logger.exception("FINAL_LINEUP_CACHE_INVALIDATION_FAILED match_id=%s", match_id)
            logger.info("FINAL_LINEUP_SUCCESS match_id=%s", match_id)
            return {"state": "success"}
        except Exception as exc:
            logger.exception("FINAL_LINEUP_ATTEMPT_FAILED match_id=%s", match_id)
            if commit_started:
                return {
                    "state": "commit_unknown",
                    "failure_category": "COMMIT_FAILURE",
                    "failure_reason": "final lineup commit outcome is unknown",
                    "attempted_at": attempted_at,
                    "provider_attempted": provider_attempted,
                }
            try:
                await db.rollback()
            except Exception:
                logger.exception("FINAL_LINEUP_ROLLBACK_FAILED match_id=%s", match_id)
            return {
                "state": "failure",
                "failure_category": "DB_FAILURE",
                "failure_reason": str(exc),
                "attempted_at": attempted_at,
                "provider_attempted": provider_attempted,
            }

    async def _persist_final_lineup_failure(
        self,
        match_id: int,
        failure_category: str,
        failure_reason: str,
        attempted_at: datetime | None,
        provider_attempted: bool,
        diagnostics: list[dict] | None = None,
        verify_commit: bool = False,
    ) -> bool:
        async with async_session() as db:
            locked, outcome = await run_with_resource_lock(
                db,
                "lineup",
                match_id,
                lambda: self._record_final_lineup_failure(
                    db,
                    match_id,
                    failure_category,
                    failure_reason,
                    attempted_at or datetime.now(timezone.utc),
                    provider_attempted,
                    diagnostics or [],
                    verify_commit,
                ),
            )
            if not locked:
                logger.info("FINAL_LINEUP_FAILURE_METADATA_SKIPPED match_id=%s reason=lock_not_acquired", match_id)
                return False
            if outcome == "success":
                try:
                    await self.cache_service.delete(make_cache_key("lineup", match_id))
                except Exception:
                    logger.exception("FINAL_LINEUP_CACHE_INVALIDATION_FAILED match_id=%s", match_id)
                logger.info("FINAL_LINEUP_SUCCESS_CONFIRMED match_id=%s", match_id)
                return True
            return False

    async def _record_final_lineup_failure(
        self,
        db: AsyncSession,
        match_id: int,
        failure_category: str,
        failure_reason: str,
        attempted_at: datetime,
        provider_attempted: bool,
        diagnostics: list[dict],
        verify_commit: bool,
    ) -> str | None:
        record = await self.final_lineup_finalization_repository.get_by_match_id(db, match_id)
        if record is None:
            return None
        if record.status in {"SUCCESS", "TERMINAL"}:
            return "success" if verify_commit else None
        record.attempt_count += 1
        if verify_commit:
            logger.warning("FINAL_LINEUP_COMMIT_NOT_CONFIRMED match_id=%s", match_id)
        terminal_categories = {
            "MASTER_RESOLUTION_FAILURE",
            "IDENTITY_BOUNDARY_VIOLATION",
        }
        if failure_category in terminal_categories or record.attempt_count >= FINAL_LINEUP_MAX_ATTEMPTS:
            if record.attempt_count >= FINAL_LINEUP_MAX_ATTEMPTS and failure_category not in terminal_categories:
                failure_category = "MAX_RETRY_ATTEMPTS_EXCEEDED"
                failure_reason = "maximum automatic lineup attempts exceeded"
            await self.final_lineup_finalization_repository.mark_terminal(
                db, record, failure_category, failure_reason, attempted_at, diagnostics
            )
        else:
            if diagnostics:
                await self.final_lineup_finalization_repository.mark_retryable(
                    db, record, failure_category, failure_reason, attempted_at,
                    failure_diagnostics=diagnostics,
                )
            else:
                await self.final_lineup_finalization_repository.mark_retryable(
                    db, record, failure_category, failure_reason, attempted_at,
                )
        await db.commit()
        logger.warning(
            "FINAL_LINEUP_FAILURE match_id=%s category=%s reason=%s",
            match_id,
            failure_category,
            failure_reason,
        )

    @staticmethod
    def _final_lineup_failure_category(result: dict) -> str:
        if result.get("failure_classification") in {
            "MISSING",
            "INVALID",
            "AMBIGUOUS",
            "TEAM_CONFLICT",
            "PERMANENT_IDENTITY_FAILURE",
        }:
            return "MASTER_RESOLUTION_FAILURE"
        reason = str(result.get("reason", ""))
        if reason == "lineup_not_available":
            return "INVALID_RESPONSE"
        return "PROVIDER_FAILURE"

    async def _upsert_leagues_from_fixtures(self, db: AsyncSession, fixtures: list[dict], seen_league_ids: set[int] | None = None) -> None:
        # Resolution-only: Fixture Sync has no League Master creation authority.
        seen = seen_league_ids if seen_league_ids is not None else set()

        provider_ids: dict[str, None] = {}
        for fixture in fixtures:
            league_info = fixture.get("league") or {}
            provider_id = league_info.get("id")
            if provider_id is None:
                continue
            provider_ids[str(provider_id)] = None

        for provider_id in provider_ids:
            master = await self.league_repository.find_by_provider_identity(db, "api-football", provider_id)
            if master is None:
                logger.warning("Skipping unresolved provider League: provider_id=%s", provider_id)
                continue
            seen.add(master.league_id)

    async def _process_sync(self, db: AsyncSession, fixtures: list) -> dict:
        result, _ = await self._process_sync_with_candidates(db, fixtures)
        return result

    @observe_sync("fixture")
    async def sync_full_season(self, db: AsyncSession, league: int, season: int) -> dict:
        allowed_ids = await self.allowed_league_repository.get_allowed_ids(db)
        if league not in allowed_ids:
            logger.debug("SKIPPED LEAGUE: league_id=%s league_name=%s", league, "requested league")
            return {"success": True, "inserted": 0, "updated": 0, "total": 0, "message": "League is not allowed for synchronization", "final_lineup_candidates": []}

        result = await self.fixture_provider.get_fixtures(league=league, season=season)
        if not result or "response" not in result:
            return {"success": False, "message": "API error", "final_lineup_candidates": []}
        fixtures = result.get("response", [])
        if not fixtures:
            return {"success": False, "message": "No fixtures found", "final_lineup_candidates": []}

        try:
            self._defer_live_cache_invalidation = True
            sync_result = await self._process_sync(db, fixtures)
            return sync_result
        finally:
            self._defer_live_cache_invalidation = False

    @observe_sync("fixture")
    async def sync_daily_fixtures(self, db: AsyncSession, target_date: str) -> dict:
        result = await self.fixture_provider.get_fixtures_by_date(target_date=target_date)
        if not result or "response" not in result:
            return {"success": False, "message": "API error", "final_lineup_candidates": []}
        fixtures = result.get("response", [])
        if not fixtures:
            return {"success": True, "message": "No matches for today", "updated": 0, "final_lineup_candidates": []}

        try:
            self._defer_live_cache_invalidation = True
            sync_result, prewarm_candidates = await self._process_sync_with_candidates(db, fixtures)
        finally:
            self._defer_live_cache_invalidation = False

        if not sync_result.get("success"):
            return sync_result

        prewarm_metrics = {
            "standings_prewarm_candidates": sync_result.get("standings_prewarm_candidates", 0),
            "standings_prewarm_synced": 0,
            "standings_prewarm_skipped": 0,
            "standings_prewarm_failed": 0,
        }

        if prewarm_candidates:
            previous_defer_value = getattr(self.standing_service, "_defer_standings_cache_invalidation", False)
            self.standing_service._defer_standings_cache_invalidation = True
            try:
                prewarm_result = await self._prewarm_missing_standings(db, prewarm_candidates)
            finally:
                self.standing_service._defer_standings_cache_invalidation = previous_defer_value
            prewarm_metrics.update(
                {
                    "standings_prewarm_candidates": prewarm_result.get("total_unique_pairs", prewarm_metrics["standings_prewarm_candidates"]),
                    "standings_prewarm_synced": prewarm_result.get("synced_pairs", 0),
                    "standings_prewarm_skipped": prewarm_result.get("already_present_pairs", 0),
                    "standings_prewarm_failed": prewarm_result.get("failed_pairs", 0),
                }
            )

        sync_result.update(prewarm_metrics)

        return sync_result

    @observe_sync("fixture")
    async def sync_live_matches(self, db: AsyncSession) -> dict:
        result = await self.fixture_provider.get_live_fixtures()
        if not result or "response" not in result:
            return {"success": False, "message": "API error", "final_lineup_candidates": []}

        fixtures = result.get("response", [])
        api_live_ids = {item["fixture"]["id"] for item in fixtures if item.get("fixture") and item["fixture"].get("id")}
        stale_threshold = datetime.now(timezone.utc) - timedelta(hours=24)
        stale_matches = await self.match_repository.get_live_stale(db, api_live_ids, stale_threshold)
        logger.debug("get_live_stale returned %d stale matches", len(stale_matches))
        for match in stale_matches[:20]:
            logger.debug("fixture_id=%s status=%s match_time=%s", match.match_id, getattr(match, "status", None), getattr(match, "match_time", None))

        if stale_matches:
            stale_ids = [str(match.match_id) for match in stale_matches]
            logger.debug("Syncing %d stale matches that are no longer in live feed", len(stale_ids))
            try:
                stale_chunks = [stale_ids[i : i + 20] for i in range(0, len(stale_ids), 20)]
                total_chunks = len(stale_chunks)
                stale_api_fixtures = []

                for chunk_index, chunk in enumerate(stale_chunks, start=1):
                    logger.debug("Fetching stale chunk %s/%s (%s ids)", chunk_index, total_chunks, len(chunk))
                    stale_resp = await self.fixture_provider.get_fixtures_by_ids(chunk)
                    chunk_response = stale_resp.get("response", []) if isinstance(stale_resp, dict) else []
                    logger.debug("Chunk returned %s fixtures", len(chunk_response))

                    print("STALE_RESULTS =", stale_resp.get("results"))
                    print("STALE_ERRORS =", stale_resp.get("errors"))
                    print("STALE_RESPONSE_LEN =", len(chunk_response))
                    if chunk_response:
                        first = chunk_response[0]
                        print("FIRST_FIXTURE_ID =", first.get("fixture", {}).get("id"))
                        print("FIRST_STATUS =", first.get("fixture", {}).get("status", {}).get("short"))
                        print("FIRST_ELAPSED =", first.get("fixture", {}).get("status", {}).get("elapsed"))
                    print(f"STALE_API_COUNT={len(chunk_response)}")

                    stale_api_fixtures.extend(chunk_response)

                if stale_api_fixtures:
                    logger.debug("total_stale_api_fixtures=%d", len(stale_api_fixtures))
                    for fixture in stale_api_fixtures:
                        fixture_info = fixture.get("fixture", {})
                        status_info = fixture_info.get("status", {})
                        logger.debug(
                            "fixture_id=%s api_status=%s elapsed=%s",
                            fixture_info.get("id"),
                            status_info.get("short"),
                            status_info.get("elapsed"),
                        )
                    fixtures.extend(stale_api_fixtures)
            except Exception as exc:
                logger.error("Failed to fetch updates for stale matches: %s", exc)

        if not fixtures:
            return {"success": True, "message": "No live matches", "updated": 0, "final_lineup_candidates": []}

        try:
            self._defer_live_cache_invalidation = True
            return await self._process_sync(db, fixtures)
        finally:
            self._defer_live_cache_invalidation = False
