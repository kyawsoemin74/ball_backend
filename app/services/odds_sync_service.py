import logging
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy import select

from app.monitoring import observe_sync
from app.models.match import Match
from app.providers.odds_provider import OddsProvider
from app.repositories.odds_repository import OddsRepository
from app.repositories.odds_refresh_state_repository import OddsRefreshStateRepository
from app.services.analytics_projection_service import AnalyticsProjectionService, log_projection_failure
from app.services.base.football_client import FootballAPIResponse
from app.services.cache_service import CacheService
from app.services.odds_reconciliation import (
    ATTEMPT_IN_PROGRESS,
    BLOCKED_RECONCILIATION_STATES,
    COMMIT_CONFIRMED_CACHE_PENDING,
    COMMIT_NOT_APPLIED_STATE,
    COMMIT_PENDING,
    MANUAL_REVIEW,
    RESOLVED,
    snapshot_fingerprint,
)

if TYPE_CHECKING:
    from app.services.odds_service import OddsService

logger = logging.getLogger(__name__)

NO_DATA = "NO_DATA"
VALIDATION_FAILED = "VALIDATION_FAILED"
IDENTITY_REJECTED = "IDENTITY_REJECTED"
PROVIDER_FAILED = "PROVIDER_FAILED"
PERSISTENCE_FAILED = "PERSISTENCE_FAILED"
CANDIDATE_READY = "CANDIDATE_READY"
COMMIT_NOT_APPLIED = "COMMIT_NOT_APPLIED"
COMMIT_UNKNOWN = "COMMIT_UNKNOWN"
COMMIT_CONFIRMED = "COMMIT_CONFIRMED"
CACHE_INVALIDATION_FAILED = "CACHE_INVALIDATION_FAILED"
SUCCESS = "SUCCESS"
RETRY_STATE_UNAVAILABLE = "RETRY_STATE_UNAVAILABLE"
RETRY_BLOCKED = "RETRY_BLOCKED"
IDENTITY_UNAVAILABLE = "IDENTITY_UNAVAILABLE"
ODDS_PROVIDER_KEY = "api-football"
ODDS_SCHEDULER_INTERVAL = timedelta(hours=3)


class OddsTransactionFailure(RuntimeError):
    """Failure that must be rolled back by the existing outer session owner."""


class OddsMatchIdentityError(ValueError):
    """The local Match does not identify the requested Odds provider fixture."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class OddsIdentityLookupError(RuntimeError):
    """The Match identity could not be verified because its lookup failed."""


class OddsSyncService:
    """Write/refresh orchestration for odds data without owning read or transport logic."""

    def __init__(
        self,
        odds_service: "OddsService",
        cache_service: CacheService | None = None,
        odds_provider: OddsProvider | None = None,
        odds_repository: OddsRepository | None = None,
        analytics_projection_service: AnalyticsProjectionService | None = None,
    ) -> None:
        if odds_service is None or not hasattr(odds_service, "build_candidate") or not hasattr(odds_service, "public_market_name"):
            from app.services.odds_service import OddsService
            from myanmar_odds.services.myanmar_odds_service import MyanmarOddsService

            compatible_service = OddsService.__new__(OddsService)
            compatible_service.client = getattr(odds_service, "client", None)
            compatible_service.cache_service = cache_service or CacheService()
            compatible_service.odds_provider = odds_provider or OddsProvider(compatible_service.client)
            compatible_service.myanmar_odds_service = MyanmarOddsService()
            compatible_service.odds_sync_service = self
            odds_service = compatible_service

        self.odds_service = odds_service
        self.cache_service = cache_service or CacheService()
        self.odds_provider = odds_provider or OddsProvider(self.odds_service.client)
        self.odds_repository = odds_repository or OddsRepository()
        self.retry_state_repository = OddsRefreshStateRepository()
        self.analytics_projection_service = analytics_projection_service or AnalyticsProjectionService()
        self.volatile_blocked_matches: set[int] = set()
        self.volatile_provider_cooldown_until: datetime | None = None

    async def check_retry_eligibility(self, db, local_match_id: int, now: datetime | None = None) -> dict:
        now = now or datetime.now(timezone.utc)
        if local_match_id in self.volatile_blocked_matches:
            return {"eligible": False, "status": RETRY_BLOCKED, "reason": "RETRY_STATE_WRITE_FAILED"}
        if self.volatile_provider_cooldown_until and self.volatile_provider_cooldown_until > now:
            return {
                "eligible": False,
                "status": RETRY_BLOCKED,
                "reason": "PROVIDER_COOLDOWN",
                "next_eligible_at": self.volatile_provider_cooldown_until,
            }
        if self.volatile_provider_cooldown_until and self.volatile_provider_cooldown_until <= now:
            self.volatile_provider_cooldown_until = None
        try:
            match_state = await self.retry_state_repository.get_by_match_id(db, local_match_id)
            reconciliation_state = (
                getattr(match_state, "reconciliation_state", "IDLE") if match_state else "IDLE"
            )
            if match_state and (
                reconciliation_state in BLOCKED_RECONCILIATION_STATES
                or (
                    match_state.last_failure_classification == COMMIT_UNKNOWN
                    and reconciliation_state != COMMIT_CONFIRMED_CACHE_PENDING
                )
            ):
                return {
                    "eligible": False,
                    "status": RETRY_BLOCKED,
                    "reason": COMMIT_UNKNOWN,
                    "cache_invalidation_pending": bool(match_state.cache_invalidation_pending),
                    "retry_due": False,
                }
            provider_state = await self.retry_state_repository.get_provider_cooldown(db, ODDS_PROVIDER_KEY)
        except Exception as exc:
            self.volatile_blocked_matches.add(local_match_id)
            logger.exception(
                "ODDS_RETRY_STATE_UNAVAILABLE",
                extra={"local_match_id": local_match_id, "failure_type": exc.__class__.__name__},
            )
            return {"eligible": False, "status": RETRY_STATE_UNAVAILABLE, "reason": "RETRY_STATE_READ_FAILED"}

        cache_pending = bool(match_state and match_state.cache_invalidation_pending)
        retry_due = bool(match_state and match_state.next_eligible_at and match_state.next_eligible_at <= now)
        if cache_pending:
            return {
                "eligible": True,
                "status": "CACHE_RECOVERY_PENDING",
                "cache_invalidation_pending": True,
                "retry_due": retry_due,
            }
        if match_state and match_state.next_eligible_at and match_state.next_eligible_at > now:
            return {
                "eligible": False,
                "status": RETRY_BLOCKED,
                "reason": "RETRY_NOT_DUE",
                "next_eligible_at": match_state.next_eligible_at,
                "cache_invalidation_pending": cache_pending,
                "retry_due": False,
            }
        if provider_state and provider_state.cooldown_until and provider_state.cooldown_until > now:
            return {
                "eligible": False,
                "status": RETRY_BLOCKED,
                "reason": "PROVIDER_COOLDOWN",
                "next_eligible_at": provider_state.cooldown_until,
                "cache_invalidation_pending": cache_pending,
                "retry_due": retry_due,
            }
        return {
            "eligible": True,
            "status": "ELIGIBLE",
            "cache_invalidation_pending": cache_pending,
            "retry_due": retry_due,
        }

    @staticmethod
    def next_scheduler_attempt(now: datetime) -> datetime:
        return now + ODDS_SCHEDULER_INTERVAL

    @staticmethod
    def parse_retry_after(value: str | None, now: datetime) -> datetime | None:
        if not value or len(value) > 128:
            return None
        text = value.strip()
        if not text:
            return None
        if text.isascii() and text.isdecimal():
            try:
                return now + timedelta(seconds=int(text))
            except (OverflowError, ValueError):
                return None
        try:
            parsed = parsedate_to_datetime(text)
        except (TypeError, ValueError, OverflowError):
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    def retry_state_update(self, result: dict, now: datetime | None = None) -> dict:
        now = now or datetime.now(timezone.utc)
        scheduled_due = self.next_scheduler_attempt(now)
        reason = str(result.get("reason") or result.get("status") or "UNKNOWN")
        classification = (
            COMMIT_UNKNOWN
            if result.get("status") == COMMIT_UNKNOWN
            else f"{result.get('status', 'UNKNOWN')}:{reason}"[:64]
        )
        provider_cooldown_until = None
        next_eligible_at = scheduled_due
        if result.get("reason") == "RATE_LIMITED":
            retry_after = self.parse_retry_after(result.get("retry_after"), now)
            if retry_after is None:
                provider_cooldown_until = scheduled_due
                next_eligible_at = scheduled_due
            else:
                if retry_after > now:
                    provider_cooldown_until = retry_after
                next_eligible_at = max(scheduled_due, retry_after)
        return {
            "classification": classification,
            "attempted_at": now,
            "next_eligible_at": next_eligible_at,
            "provider_key": ODDS_PROVIDER_KEY,
            "provider_cooldown_until": provider_cooldown_until,
            "http_status": result.get("http_status"),
        }

    def mark_retry_state_unavailable(
        self,
        local_match_id: int,
        exc: Exception,
        provider_cooldown_until: datetime | None = None,
    ) -> None:
        self.volatile_blocked_matches.add(local_match_id)
        if provider_cooldown_until is not None:
            self.volatile_provider_cooldown_until = max(
                self.volatile_provider_cooldown_until or provider_cooldown_until,
                provider_cooldown_until,
            )
        logger.critical(
            "ODDS_RETRY_STATE_WRITE_FAILED",
            extra={
                "local_match_id": local_match_id,
                "failure_type": exc.__class__.__name__,
                "durable_deferral": False,
            },
        )

    def mark_retry_state_persisted(self, local_match_id: int) -> None:
        self.volatile_blocked_matches.discard(local_match_id)

    async def validate_match_identity(self, db, local_match_id: int, provider_fixture_id: int) -> int:
        if not isinstance(local_match_id, int) or isinstance(local_match_id, bool) or local_match_id <= 0:
            raise OddsMatchIdentityError("INVALID_LOCAL_MATCH_ID")
        if (
            not isinstance(provider_fixture_id, int)
            or isinstance(provider_fixture_id, bool)
            or provider_fixture_id <= 0
        ):
            raise OddsMatchIdentityError("INVALID_PROVIDER_FIXTURE_ID")

        try:
            match = await db.scalar(
                select(Match).where(Match.local_match_id == local_match_id)
            )
        except Exception as exc:
            raise OddsIdentityLookupError("Odds Match identity lookup failed") from exc

        if match is None:
            raise OddsMatchIdentityError("MATCH_NOT_FOUND")
        if getattr(match, "provider", None) != ODDS_PROVIDER_KEY:
            raise OddsMatchIdentityError("MATCH_PROVIDER_MISMATCH")

        stored_fixture_id = getattr(match, "provider_fixture_id", None)
        if (
            not isinstance(stored_fixture_id, int)
            or isinstance(stored_fixture_id, bool)
            or stored_fixture_id <= 0
        ):
            raise OddsMatchIdentityError("INVALID_STORED_PROVIDER_FIXTURE_ID")
        if stored_fixture_id != provider_fixture_id:
            raise OddsMatchIdentityError("PROVIDER_FIXTURE_ID_MISMATCH")
        return stored_fixture_id

    async def prepare_refresh_attempt(
        self,
        db,
        local_match_id: int,
        provider_fixture_id: int,
    ) -> str:
        await self.validate_match_identity(db, local_match_id, provider_fixture_id)
        odds_rows = await self.odds_repository.get_fixture_odds(db, local_match_id)
        analytics_rows = await self.analytics_projection_service.odds_repository.list_by_match(
            db,
            local_match_id,
        )
        now = datetime.now(timezone.utc)
        attempt_id = str(uuid4())
        await self.retry_state_repository.begin_attempt(
            db,
            local_match_id,
            attempt_id=attempt_id,
            provider_fixture_id=provider_fixture_id,
            pre_attempt_odds_fingerprint=snapshot_fingerprint(odds_rows),
            pre_attempt_analytics_fingerprint=snapshot_fingerprint(analytics_rows),
            attempted_at=now,
        )
        return attempt_id

    async def reconcile_refresh_attempt(self, db, state) -> dict:
        match = await db.scalar(select(Match).where(Match.local_match_id == state.match_id))
        if (
            match is None
            or match.provider != ODDS_PROVIDER_KEY
            or match.provider_fixture_id != state.provider_fixture_id
        ):
            return {"state": MANUAL_REVIEW, "reason": "MATCH_IDENTITY_UNVERIFIED"}

        odds_rows = await self.odds_repository.get_fixture_odds(db, state.match_id)
        analytics_rows = await self.analytics_projection_service.odds_repository.list_by_match(
            db,
            state.match_id,
        )
        current_odds_fingerprint = snapshot_fingerprint(odds_rows)
        current_analytics_fingerprint = snapshot_fingerprint(analytics_rows)

        candidate_matches = (
            state.candidate_fingerprint is not None
            and current_odds_fingerprint == state.candidate_fingerprint
            and current_analytics_fingerprint == state.candidate_fingerprint
        )
        if candidate_matches:
            return {"state": COMMIT_CONFIRMED_CACHE_PENDING, "reason": "BOTH_CANDIDATE_SNAPSHOTS_MATCH"}

        pre_attempt_matches = (
            current_odds_fingerprint == state.pre_attempt_odds_fingerprint
            and current_analytics_fingerprint == state.pre_attempt_analytics_fingerprint
        )
        if pre_attempt_matches:
            return {"state": COMMIT_NOT_APPLIED_STATE, "reason": "BOTH_PRE_ATTEMPT_SNAPSHOTS_MATCH"}

        return {"state": MANUAL_REVIEW, "reason": "SNAPSHOTS_DIVERGED"}

    async def resolve_attempt_without_candidate(
        self,
        db,
        local_match_id: int,
        attempt_id: str,
        classification: str,
    ) -> None:
        resolved = await self.retry_state_repository.resolve_reconciliation(
            db,
            local_match_id,
            attempt_id=attempt_id,
            reconciliation_state=RESOLVED,
            reconciled_at=datetime.now(timezone.utc),
            classification=classification[:64],
        )
        if not resolved:
            raise RuntimeError("Odds refresh attempt could not be resolved")

    @staticmethod
    def is_commit_ambiguous(exc: Exception) -> bool:
        # The outcome of any exception raised after commit starts is verified
        # from a fresh session rather than inferred from the exception class.
        return True

    async def verify_commit_unknown(
        self,
        db,
        local_match_id: int,
        provider_fixture_id: int,
        _persistence_rows: list[dict],
    ) -> dict:
        state = await self.retry_state_repository.get_by_match_id(db, local_match_id)
        if (
            state is None
            or state.provider_fixture_id != provider_fixture_id
            or state.reconciliation_state not in {
                COMMIT_PENDING,
                COMMIT_CONFIRMED_CACHE_PENDING,
                ATTEMPT_IN_PROGRESS,
            }
        ):
            return {"state": COMMIT_UNKNOWN, "reason": "MATCH_IDENTITY_UNVERIFIED"}

        reconciliation = await self.reconcile_refresh_attempt(db, state)
        if reconciliation["state"] == COMMIT_CONFIRMED_CACHE_PENDING:
            return {"state": COMMIT_CONFIRMED, "reason": reconciliation["reason"]}
        if reconciliation["state"] == COMMIT_NOT_APPLIED_STATE:
            return {"state": COMMIT_NOT_APPLIED, "reason": reconciliation["reason"]}
        if reconciliation["state"] == MANUAL_REVIEW:
            return {"state": COMMIT_UNKNOWN, "reason": reconciliation["reason"]}
        return {"state": COMMIT_UNKNOWN, "reason": "ODDS_ANALYTICS_SNAPSHOT_MISMATCH"}

    @staticmethod
    def _outcome(status: str, reason: str, **fields) -> dict:
        return {
            "status": status,
            "reason": reason,
            "success": status == SUCCESS,
            **fields,
        }

    @observe_sync("odds")
    async def refresh_odds(
        self,
        db,
        fixture_id: int,
        cache_key: str,
        pre_match_ttl: int,
        *,
        local_match_id: int | None = None,
        attempt_id: str | None = None,
    ) -> dict:
        if not isinstance(fixture_id, int) or isinstance(fixture_id, bool) or fixture_id <= 0:
            return self._outcome(IDENTITY_REJECTED, "INVALID_PROVIDER_FIXTURE_ID")
        if not isinstance(local_match_id, int) or isinstance(local_match_id, bool) or local_match_id <= 0:
            return self._outcome(IDENTITY_REJECTED, "INVALID_LOCAL_MATCH_ID")

        try:
            validated_fixture_id = await self.validate_match_identity(db, local_match_id, fixture_id)
        except OddsMatchIdentityError as exc:
            logger.warning(
                "ODDS_MATCH_IDENTITY_REJECTED",
                extra={
                    "local_match_id": local_match_id,
                    "provider_fixture_id": fixture_id,
                    "expected_provider": ODDS_PROVIDER_KEY,
                    "reason": exc.reason,
                },
            )
            return self._outcome(IDENTITY_REJECTED, exc.reason)
        except OddsIdentityLookupError as exc:
            logger.exception(
                "ODDS_MATCH_IDENTITY_LOOKUP_FAILED",
                extra={
                    "local_match_id": local_match_id,
                    "provider_fixture_id": fixture_id,
                    "failure_type": exc.__class__.__name__,
                },
            )
            return self._outcome(IDENTITY_UNAVAILABLE, "MATCH_IDENTITY_LOOKUP_FAILED")

        try:
            result = await self.odds_provider.get_match_odds(validated_fixture_id)
        except Exception as exc:
            log_projection_failure("odds", {"match_id": local_match_id}, "provider_request_failure")
            logger.exception(
                "ODDS_PROVIDER_FAILED",
                extra={
                    "local_match_id": local_match_id,
                    "provider_fixture_id": fixture_id,
                    "failure_type": exc.__class__.__name__,
                },
            )
            return self._outcome(PROVIDER_FAILED, "PROVIDER_REQUEST_FAILED")

        if isinstance(result, FootballAPIResponse):
            if result.exception_type or (result.status_code is None and result.payload is None):
                category = "TIMEOUT" if result.exception_type and "timeout" in result.exception_type.lower() else "PROVIDER_REQUEST_FAILED"
                log_projection_failure("odds", {"match_id": local_match_id}, "provider_request_failure")
                return self._outcome(
                    PROVIDER_FAILED,
                    category,
                    http_status=result.status_code,
                    retry_after=result.retry_after,
                )
            if (
                (result.status_code is not None and result.status_code >= 400)
                or result.error_code
                or result.error_message
                or (isinstance(result.payload, dict) and result.payload.get("errors"))
            ):
                status_code = result.status_code
                reason = "RATE_LIMITED" if status_code == 429 else "SERVER_ERROR" if status_code and status_code >= 500 else "PROVIDER_API_ERROR"
                log_projection_failure("odds", {"match_id": local_match_id}, "provider_api_error")
                return self._outcome(
                    PROVIDER_FAILED,
                    reason,
                    http_status=status_code,
                    retry_after=result.retry_after,
                )
            result = result.payload

        if not isinstance(result, dict) or "response" not in result:
            log_projection_failure("odds", {"match_id": local_match_id}, "provider_response_invalid")
            return self._outcome(PROVIDER_FAILED, "MALFORMED_PROVIDER_ENVELOPE")
        if result.get("errors"):
            log_projection_failure("odds", {"match_id": local_match_id}, "provider_api_error")
            return self._outcome(PROVIDER_FAILED, "PROVIDER_API_ERROR")

        responses = result.get("response")
        if not isinstance(responses, list):
            return self._outcome(PROVIDER_FAILED, "MALFORMED_PROVIDER_ENVELOPE")
        if not responses:
            return self._outcome(NO_DATA, "EMPTY_PROVIDER_RESPONSE")
        if len(responses) != 1 or not isinstance(responses[0], dict):
            return self._outcome(VALIDATION_FAILED, "AMBIGUOUS_FIXTURE_RESPONSE")

        item = responses[0]
        fixture = item.get("fixture")
        response_fixture_id = fixture.get("id") if isinstance(fixture, dict) else None
        if (
            not isinstance(response_fixture_id, int)
            or isinstance(response_fixture_id, bool)
            or response_fixture_id != validated_fixture_id
        ):
            logger.warning(
                "ODDS_PROVIDER_RESPONSE_IDENTITY_REJECTED",
                extra={
                    "local_match_id": local_match_id,
                    "provider_fixture_id": validated_fixture_id,
                    "response_fixture_id": response_fixture_id,
                    "expected_provider": ODDS_PROVIDER_KEY,
                },
            )
            return self._outcome(IDENTITY_REJECTED, "PROVIDER_FIXTURE_ID_MISMATCH")
        bookmakers = item.get("bookmakers")
        if not isinstance(bookmakers, list):
            return self._outcome(VALIDATION_FAILED, "MALFORMED_BOOKMAKERS")
        matching_bookmakers = [bookmaker for bookmaker in bookmakers if isinstance(bookmaker, dict) and bookmaker.get("id") == 11]
        if not matching_bookmakers:
            return self._outcome(NO_DATA, "PRIMARY_BOOKMAKER_NOT_AVAILABLE")
        if len(matching_bookmakers) != 1:
            return self._outcome(VALIDATION_FAILED, "DUPLICATE_PRIMARY_BOOKMAKER")

        try:
            candidate, diagnostics = self.odds_service.build_candidate(matching_bookmakers[0])
        except ValueError as exc:
            diagnostic_rows = getattr(exc, "diagnostics", [])
            logger.warning(
                "ODDS_CANDIDATE_REJECTED",
                extra={
                    "local_match_id": local_match_id,
                    "provider_fixture_id": fixture_id,
                    "reason": getattr(exc, "reason", str(exc)),
                    "diagnostics": diagnostic_rows,
                },
            )
            return self._outcome(
                VALIDATION_FAILED,
                getattr(exc, "reason", "INVALID_CANDIDATE"),
                diagnostics=diagnostic_rows,
            )

        if not candidate:
            return self._outcome(VALIDATION_FAILED, "EMPTY_VALIDATED_CANDIDATE", diagnostics=diagnostics)

        now_utc = datetime.now(timezone.utc)
        persistence_rows = [
            {
                "fixture_id": local_match_id,
                "bookmaker_name": row["bookmaker_name"],
                "market_name": row["market_name"],
                "selection": row["selection"],
                "odd_value": row["odd_value"],
                "myanmar_odd": row["myanmar_odd"],
                "last_updated": now_utc,
            }
            for row in candidate
        ]

        try:
            if attempt_id is not None:
                await self.retry_state_repository.set_candidate_fingerprint(
                    db,
                    local_match_id,
                    attempt_id=attempt_id,
                    candidate_fingerprint=snapshot_fingerprint(persistence_rows),
                    updated_at=now_utc,
                )
            await self.odds_repository.replace_fixture_odds(db, local_match_id, persistence_rows)
            await db.flush()
            projection_rows = [
                {**row, "observed_at": now_utc}
                for row in persistence_rows
            ]
            projection = await self.analytics_projection_service.project_odds(db, local_match_id, projection_rows)
            if (
                not projection.get("success")
                or projection.get("accepted_count") != len(persistence_rows)
                or projection.get("analytics_count") != len(persistence_rows)
            ):
                raise OddsTransactionFailure(
                    f"Analytics projection rejected candidate: {projection.get('reason', 'row count mismatch')}"
                )
        except Exception as exc:
            log_projection_failure("odds", {"match_id": local_match_id}, "persistence_or_projection_failure")
            logger.exception(
                "ODDS_PERSISTENCE_FAILED",
                extra={
                    "local_match_id": local_match_id,
                    "provider_fixture_id": fixture_id,
                    "failure_type": exc.__class__.__name__,
                },
            )
            return self._outcome(PERSISTENCE_FAILED, "ODDS_OR_ANALYTICS_WRITE_FAILED")

        odds_data = [
            {
                "bookmaker": row["bookmaker_name"],
                "market": self.odds_service.public_market_name(row["market_name"]),
                "selection": row["selection"],
                "odd": row["odd_value"],
                "myanmar_odd": row["myanmar_odd"],
                "updated_at": now_utc.isoformat(),
            }
            for row in persistence_rows
        ]
        return self._outcome(
            CANDIDATE_READY,
            "VALIDATED",
            source="api",
            odds=odds_data,
            cached=False,
            match_started=False,
            updated=len(persistence_rows),
            analytics=projection,
            _transaction_context={
                "local_match_id": local_match_id,
                "provider_fixture_id": fixture_id,
                "attempt_id": attempt_id,
                "persistence_rows": persistence_rows,
            },
        )
