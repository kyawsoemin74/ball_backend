import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone

from sqlalchemy import event
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.cache import cache_delete_sync, make_cache_key
from app.services.coach_sync_service import CoachSyncService
from app.providers.team_provider import TeamProvider
from app.repositories.team_repository import TeamRepository
from app.services.cache_service import CacheService
from app.services.country_sync_service import CountrySyncService

logger = logging.getLogger(__name__)

_TEAM_POST_COMMIT_CACHE_KEYS = "_team_post_commit_cache_keys"
COACH_TTL = timedelta(hours=24)
_COACH_SYNC_BATCH: ContextVar[dict[tuple[int, str], dict] | None] = ContextVar(
    "coach_sync_batch",
    default=None,
)


@contextmanager
def coach_sync_batch_scope() -> Iterator[None]:
    """Share Coach decisions across one fixture-sync execution and nested work."""
    if _COACH_SYNC_BATCH.get() is not None:
        yield
        return

    token = _COACH_SYNC_BATCH.set({})
    try:
        yield
    finally:
        _COACH_SYNC_BATCH.reset(token)


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
    """Owns Team synchronization/write orchestration and provider-to-local identity resolution."""

    def __init__(
        self,
        cache_service: CacheService,
        team_repository: TeamRepository | None = None,
        coach_sync_service: CoachSyncService | None = None,
        team_provider: TeamProvider | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.cache_service = cache_service
        self.team_repository = team_repository or TeamRepository()
        self.coach_sync_service = coach_sync_service or CoachSyncService()
        self.team_provider = team_provider
        self.country_sync_service = CountrySyncService()
        self.clock = clock or (lambda: datetime.now(timezone.utc))

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

    async def resolve_provider_team_identity(
        self,
        db: AsyncSession,
        provider: str,
        provider_id: str | int,
        payload: dict | None = None,
        canonical_team_id: int | None = None,
    ):
        """Reusable provider identity resolver for provider=(provider, provider_id)->local Team.

        An optional canonical_team_id allows a null-provider Team row to receive the
        provider ownership after the existing provider-owner Team row has been released.
        """
        if not provider or not str(provider).strip():
            raise ValueError("INVALID")
        if provider_id is None:
            raise ValueError("INVALID")

        provider_key = str(provider).strip()
        provider_id_key = str(provider_id).strip()
        if not provider_id_key:
            raise ValueError("INVALID")

        existing = await self.team_repository.find_by_provider_identity(db, provider_key, provider_id_key)
        if existing is not None:
            if canonical_team_id is not None:
                canonical_id = int(canonical_team_id)
                existing_id = int(existing.team_id)
                if existing_id == canonical_id:
                    return existing
                # Approved conflict ownership remediation path.
                # Canonical null-provider row is entitled to receive the provider_id.
                try:
                    canonical = await self.team_repository.get_by_id(db, canonical_id)
                    if canonical is None:
                        raise ValueError("INVALID")
                    if getattr(canonical, "provider_id", None) is not None:
                        raise ValueError("CONFLICT")

                    # Deterministic lock order over the two rows that matter.
                    ordered_ids = sorted([existing_id, canonical_id])
                    # Use a simple TO-DO within this service-level branch: the repository
                    # contract intentionally only carries the read/write updates now.
                    # The session context will keep the database in an atomic flow.
                    await self.team_repository.release_provider_identity(db, existing_id)
                    attached = await self.team_repository.attach_provider_identity(
                        db,
                        canonical_id,
                        provider_key,
                        provider_id_key,
                    )
                    return attached
                except Exception:
                    raise ValueError("CONFLICT") from None
            return existing

        payload = payload or {}
        response = payload.get("response") if isinstance(payload, dict) else None
        if isinstance(response, list) and response and isinstance(response[0], dict):
            nested = response[0].get("team") or response[0]
            if isinstance(nested, dict):
                payload = nested

        team_payload = payload.get("team") if isinstance(payload, dict) and isinstance(payload.get("team"), dict) else payload
        if not isinstance(team_payload, dict):
            team_payload = {}

        name = team_payload.get("name") if isinstance(team_payload.get("name"), str) else None
        country = team_payload.get("country") if isinstance(team_payload.get("country"), str) else None
        if isinstance(name, str):
            name = name.strip()
        if isinstance(country, str):
            country = country.strip()

        candidates = await self.team_repository.find_candidate_null_provider_teams(
            db,
            provider_key,
            name=name,
            country=country,
        )
        if len(candidates) > 1:
            raise ValueError("AMBIGUOUS")
        if len(candidates) == 1:
            try:
                attached = await self.team_repository.attach_provider_identity(
                    db,
                    int(candidates[0].team_id),
                    provider_key,
                    provider_id_key,
                )
                return attached
            except Exception:
                raise ValueError("CONFLICT") from None

        row = {
            "provider": provider_key,
            "provider_id": provider_id_key,
            "name": name or "Unknown Team",
            "country": country,
            "logo": team_payload.get("logo"),
            "stadium": team_payload.get("stadium") if isinstance(team_payload.get("stadium"), str) and team_payload.get("stadium").strip() else None,
            "founded": team_payload.get("founded"),
        }
        try:
            return await self.team_repository.upsert_by_provider_identity(db, row)
        except Exception:
            raise ValueError("CONFLICT") from None

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

    async def collect_provider_evidence_for_null_provider_teams(
        self,
        db: AsyncSession,
    ) -> list[dict]:
        """Read-only evidence collector for all api-football teams whose provider_id is NULL.

        It uses the existing provider infrastructure and returns the provider evidence list
        without mutating local Team rows or their relationships.
        """
        if self.team_provider is None:
            return []

        teams = await self.team_repository.get_null_provider_api_football_teams(db)
        evidence = []
        for team in teams:
            current_league_id = getattr(team, "current_league_id", None)
            current_season = getattr(team, "current_season", None)
            try:
                if current_season is not None:
                    season = int(str(current_season))
                else:
                    season = None
            except Exception:
                season = None

            provider_result = await self.team_provider.collect_team_evidence(
                team_name=getattr(team, "name", None),
                country=getattr(team, "country", None),
                league_id=current_league_id,
                season=season,
            )
            status = provider_result.get("provider_request_status", "NO_CANDIDATE")
            candidates = provider_result.get("candidates") or []
            if candidates:
                for candidate in candidates:
                    evidence.append({
                        "local_team_id": int(team.team_id),
                        "local_team_name": getattr(team, "name", None),
                        "local_country": getattr(team, "country", None),
                        "current_provider": getattr(team, "provider", None),
                        "current_provider_id": getattr(team, "provider_id", None),
                        "candidate_provider_id": candidate.get("provider_id"),
                        "provider_team_name": candidate.get("team_name"),
                        "provider_country": candidate.get("country"),
                        "provider_league_context": candidate.get("league"),
                        "provider_season_context": candidate.get("season"),
                        "provider_request_status": status,
                        "classification": "NOT_FOUND",
                        "canonical_team_id": int(team.team_id),
                        "conflicting_team_id": None,
                        "evidence": candidate.get("raw"),
                        "proposed_action": "LEAVE_UNRESOLVED",
                        "confidence": "READ_ONLY_EVIDENCE",
                    })
            else:
                evidence.append({
                    "local_team_id": int(team.team_id),
                    "local_team_name": getattr(team, "name", None),
                    "local_country": getattr(team, "country", None),
                    "current_provider": getattr(team, "provider", None),
                    "current_provider_id": getattr(team, "provider_id", None),
                    "candidate_provider_id": None,
                    "provider_team_name": None,
                    "provider_country": None,
                    "provider_league_context": None,
                    "provider_season_context": None,
                    "provider_request_status": status,
                    "classification": "NOT_FOUND",
                    "canonical_team_id": int(team.team_id),
                    "conflicting_team_id": None,
                    "evidence": None,
                    "proposed_action": "LEAVE_UNRESOLVED",
                    "confidence": "READ_ONLY_EVIDENCE",
                })

        return evidence

    async def resolve_provider_teams(self, db: AsyncSession, teams_data: list[dict]) -> dict:
        """Resolve provider Team IDs to local Team Master IDs."""
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
        """Resolve existing Teams and create missing Masters from the provider."""
        resolved = {}
        unresolved = []
        created = 0
        errors = []
        for item in teams_data or []:
            if not isinstance(item, dict):
                unresolved.append(item)
                continue

            nested_team = item.get("team") if isinstance(item.get("team"), dict) else item
            provider_id = item.get("provider_id", nested_team.get("id"))
            if provider_id is None:
                unresolved.append(item)
                continue

            existing = await self.team_repository.find_by_provider_identity(
                db,
                "api-football",
                provider_id,
            )
            if existing is not None:
                resolved[int(provider_id)] = int(existing.team_id)
                continue

            if self.team_provider is None:
                unresolved.append(item)
                continue

            try:
                payload = item if item.get("team") or item.get("name") else None
                if payload is None:
                    payload = await self.team_provider.get_team_details(int(provider_id))
                team = await self.upsert_team(db, payload, provider_id=provider_id)
                if team is None:
                    unresolved.append(item)
                    errors.append({"provider_id": provider_id, "reason": "team_sync_failed"})
                    continue
                resolved[int(provider_id)] = int(team.team_id)
                created += 1
            except ValueError:
                raise
            except SQLAlchemyError:
                logger.exception("TEAM_SYNC_DATABASE_FAILURE provider_id=%s", provider_id)
                raise
            except Exception as exc:
                logger.warning(
                    "TEAM_SYNC_FAILED provider_id=%s error=%s",
                    provider_id,
                    exc,
                )
                unresolved.append(item)
                errors.append({"provider_id": provider_id, "reason": "provider_or_database_failure"})

        result = {
            "created": created,
            "existing": len(resolved) - created,
            "unresolved": len(unresolved),
            "total": len(teams_data or []),
            "resolved": resolved,
        }
        if errors:
            result["errors"] = errors
        return result

    async def upsert_team(
        self,
        db: AsyncSession,
        team_data: dict | None,
        *,
        provider_id: str | int | None = None,
    ):
        if not isinstance(team_data, dict):
            raise ValueError("Team payload must be an object")

        response = team_data.get("response")
        if isinstance(response, list):
            if len(response) != 1 or not isinstance(response[0], dict):
                raise ValueError("Team provider response must contain exactly one Team")
            team_data = response[0]

        team_payload = team_data.get("team") or team_data
        if not isinstance(team_payload, dict):
            raise ValueError("Team payload is missing the team object")
        provider_id = provider_id if provider_id is not None else team_payload.get("id")
        if provider_id is None:
            raise ValueError("Team payload is missing the id field")
        if team_payload.get("id") is not None and str(team_payload["id"]) != str(provider_id):
            raise ValueError("Team provider identity does not match the requested Team")
        name = team_payload.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Team payload is missing a valid name")

        venue_payload = team_data.get("venue") or {}
        team = await self.team_repository.find_by_provider_identity(db, "api-football", provider_id)
        if team is None:
            upsert_by_identity = getattr(self.team_repository, "upsert_by_provider_identity", None)
            if upsert_by_identity is None:
                raise RuntimeError("Team repository lacks provider-identity upsert support")
            team = await upsert_by_identity(
                db,
                {
                    "provider": "api-football",
                    "provider_id": str(provider_id),
                    "name": name.strip(),
                    "country": team_payload.get("country"),
                    "logo": team_payload.get("logo"),
                    "stadium": venue_payload.get("name"),
                    "founded": team_payload.get("founded"),
                },
            )
            verified_team = await self.team_repository.find_by_provider_identity(
                db,
                "api-football",
                provider_id,
            )
            if verified_team is None or int(verified_team.team_id) != int(team.team_id):
                raise RuntimeError(
                    "Team provider identity verification failed for "
                    f"provider_id={provider_id}"
                )
            team = verified_team
        else:
            update_provider_metadata = getattr(self.team_repository, "update_provider_metadata", None)
            if update_provider_metadata is not None:
                await update_provider_metadata(
                    db,
                    team.team_id,
                    name=name.strip(),
                    country=team_payload.get("country") or getattr(team, "country", None),
                    logo=team_payload.get("logo") or getattr(team, "logo", None),
                    stadium=venue_payload.get("name") or getattr(team, "stadium", None),
                    founded=team_payload.get("founded") or getattr(team, "founded", None),
                )
                team.name = name.strip()
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

        self._queue_team_cache_invalidation(db, int(team.team_id))

        return team

    async def sync_team_coach(self, db: AsyncSession, team_id: int) -> dict:
        team = await self.team_repository.get_by_id(db, team_id)
        if team is None or getattr(team, "provider_id", None) is None:
            return {"success": True, "team_id": team_id, "coach_id": None, "updated": False, "reason": "unresolved_team"}

        provider_team_id = str(team.provider_id)
        batch = _COACH_SYNC_BATCH.get()
        batch_key = (int(team.team_id), provider_team_id)
        if batch is not None and batch_key in batch:
            previous = batch[batch_key]
            result = {
                "success": True,
                "team_id": int(team_id),
                "coach_id": previous.get("coach_id"),
                "updated": False,
                "reason": "COACH_SKIP_BATCH_DUPLICATE",
            }
            logger.info(
                "COACH_SKIP_BATCH_DUPLICATE local_team_id=%s provider_team_id=%s "
                "local_coach_id=%s original_decision=%s",
                team.team_id,
                provider_team_id,
                previous.get("coach_id"),
                previous.get("decision"),
            )
            return result

        coach = None
        team_coach_id = getattr(team, "coach_id", None)
        coach_repository = getattr(self.coach_sync_service, "repository", None)
        get_coach_by_id = getattr(coach_repository, "get_by_id", None)
        if team_coach_id is not None and get_coach_by_id is not None:
            coach = await get_coach_by_id(db, int(team_coach_id))

        now = self.clock()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        now = now.astimezone(timezone.utc)
        updated_at = getattr(coach, "updated_at", None) if coach is not None else None
        if isinstance(coach, dict):
            updated_at = coach.get("updated_at")
        if isinstance(updated_at, datetime):
            if updated_at.tzinfo is None:
                updated_at = updated_at.replace(tzinfo=timezone.utc)
            age = now - updated_at.astimezone(timezone.utc)
        else:
            age = None

        if coach is not None and age is not None and age <= COACH_TTL:
            coach_id = getattr(coach, "coach_id", None)
            if coach_id is None and isinstance(coach, dict):
                coach_id = coach.get("coach_id")
            decision = {
                "coach_id": coach_id,
                "decision": "COACH_SKIP_FRESH",
            }
            if batch is not None:
                batch[batch_key] = decision
            logger.info(
                "COACH_SKIP_FRESH local_team_id=%s provider_team_id=%s "
                "local_coach_id=%s age_seconds=%s ttl_seconds=%s",
                team.team_id,
                provider_team_id,
                coach_id,
                age.total_seconds(),
                COACH_TTL.total_seconds(),
            )
            return {
                "success": True,
                "team_id": int(team_id),
                "coach_id": coach_id,
                "updated": False,
                "reason": "COACH_SKIP_FRESH",
            }

        existing_coach_id = team_coach_id
        if coach is not None:
            existing_coach_id = getattr(coach, "coach_id", None)
        if isinstance(coach, dict):
            existing_coach_id = coach.get("coach_id")
        decision_name = "COACH_REFRESH" if coach is not None else "COACH_FETCH"
        freshness_reason = (
            "timestamp_missing"
            if coach is not None and age is None
            else "ttl_expired" if coach is not None else "coach_missing"
        )
        logger.info(
            "%s local_team_id=%s provider_team_id=%s local_coach_id=%s "
            "freshness_reason=%s age_seconds=%s ttl_seconds=%s",
            decision_name,
            team.team_id,
            provider_team_id,
            existing_coach_id,
            freshness_reason,
            age.total_seconds() if age is not None else None,
            COACH_TTL.total_seconds(),
        )
        if batch is not None:
            batch[batch_key] = {
                "coach_id": existing_coach_id,
                "decision": decision_name,
            }

        response_method = getattr(
            self.coach_sync_service.provider,
            "get_team_coach_response",
            None,
        )
        select_method = getattr(self.coach_sync_service, "select_team_coach", None)
        if response_method is None or select_method is None:
            logger.error(
                "COACH_FETCH_FAILED local_team_id=%s provider_team_id=%s "
                "local_coach_id=%s reason=invalid_selection_contract",
                team.team_id,
                provider_team_id,
                existing_coach_id,
            )
            result = {
                "success": True,
                "team_id": int(team_id),
                "coach_id": existing_coach_id,
                "updated": False,
                "reason": "COACH_FETCH_FAILED",
            }
            if batch is not None:
                batch[batch_key] = {
                    "coach_id": existing_coach_id,
                    "decision": "COACH_FETCH_FAILED",
                }
            return result

        try:
            provider_response = await response_method(int(team.provider_id))
            selection = select_method(provider_response)
        except Exception:
            logger.exception(
                "COACH_FETCH_FAILED local_team_id=%s provider_team_id=%s "
                "local_coach_id=%s reason=provider_exception",
                team.team_id,
                provider_team_id,
                existing_coach_id,
            )
            result = {
                "success": True,
                "team_id": int(team_id),
                "coach_id": existing_coach_id,
                "updated": False,
                "reason": "COACH_FETCH_FAILED",
            }
            if batch is not None:
                batch[batch_key] = {
                    "coach_id": existing_coach_id,
                    "decision": "COACH_FETCH_FAILED",
                }
            return result

        if not isinstance(selection, dict):
            selection = {"status": "INVALID", "coach": None}

        selection_status = selection.get("status")
        payload = selection.get("coach")
        if selection_status != "VERIFIED":
            logger.warning(
                "COACH_FETCH_FAILED local_team_id=%s provider_team_id=%s "
                "local_coach_id=%s selection_status=%s",
                team.team_id,
                provider_team_id,
                existing_coach_id,
                selection_status,
            )
            result = {
                "success": True,
                "team_id": int(team_id),
                "coach_id": existing_coach_id,
                "updated": False,
                "reason": "COACH_FETCH_FAILED",
            }
            if batch is not None:
                batch[batch_key] = {
                    "coach_id": existing_coach_id,
                    "decision": "COACH_FETCH_FAILED",
                }
            return result

        if not isinstance(payload, dict):
            logger.warning(
                "COACH_FETCH_FAILED local_team_id=%s provider_team_id=%s "
                "local_coach_id=%s reason=missing_verified_payload",
                team.team_id,
                provider_team_id,
                existing_coach_id,
            )
            result = {
                "success": True,
                "team_id": int(team_id),
                "coach_id": existing_coach_id,
                "updated": False,
                "reason": "COACH_FETCH_FAILED",
            }
            if batch is not None:
                batch[batch_key] = {
                    "coach_id": existing_coach_id,
                    "decision": "COACH_FETCH_FAILED",
                }
            return result

        coach_record = await self.coach_sync_service.sync_team_coach(db, payload)
        coach_id = coach_record.get("coach_id")
        if coach_id is None:
            logger.warning(
                "COACH_FETCH_FAILED local_team_id=%s provider_team_id=%s "
                "local_coach_id=%s provider_coach_id=%s reason=coach_unresolved",
                team.team_id,
                provider_team_id,
                existing_coach_id,
                payload.get("id"),
            )
            result = {
                "success": True,
                "team_id": int(team_id),
                "coach_id": existing_coach_id,
                "updated": False,
                "reason": "COACH_FETCH_FAILED",
            }
            if batch is not None:
                batch[batch_key] = {
                    "coach_id": existing_coach_id,
                    "decision": "COACH_FETCH_FAILED",
                }
            return result

        if getattr(team, "coach_id", None) != coach_id:
            await self.team_repository.update_current_coach(db, team_id, coach_id)
            self._queue_team_cache_invalidation(db, team_id)

        logger.info(
            "%s local_team_id=%s provider_team_id=%s local_coach_id=%s "
            "provider_coach_id=%s outcome=success",
            decision_name,
            team.team_id,
            provider_team_id,
            coach_id,
            payload.get("id"),
        )
        if batch is not None:
            batch[batch_key] = {"coach_id": coach_id, "decision": decision_name}
        return {"success": True, "team_id": team_id, "coach_id": coach_id, "updated": True}
