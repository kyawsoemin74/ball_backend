import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from collections.abc import Mapping

from sqlalchemy import select
from sqlalchemy.exc import DBAPIError

from app.models.match import Match
from app.providers.odds_provider import OddsProvider
from app.repositories.odds_repository import OddsRepository
from app.services.base.football_client import FootballAPIResponse
from app.services.cache_service import CacheService
from app.services.analytics_projection_service import AnalyticsProjectionService, log_projection_failure
from app.services.odds_identity import (
    classify_market_id,
    normalize_bookmaker_name,
    normalize_odds_response,
    normalize_odds_value,
)
from app.monitoring import observe_sync

if TYPE_CHECKING:
    from app.services.odds_service import OddsService

logger = logging.getLogger(__name__)

PROVIDER_EMPTY = "PROVIDER_EMPTY"
PROVIDER_RATE_LIMITED = "PROVIDER_RATE_LIMITED"
PROVIDER_QUOTA_EXHAUSTED = "PROVIDER_QUOTA_EXHAUSTED"
PROVIDER_REQUEST_FAILED = "PROVIDER_REQUEST_FAILED"
PROVIDER_FIXTURE_MISMATCH = "PROVIDER_FIXTURE_MISMATCH"
INVALID_PROVIDER = "INVALID_PROVIDER"
INVALID_PROVIDER_FIXTURE_ID = "INVALID_PROVIDER_FIXTURE_ID"
INVALID_ODDS_PAYLOAD = "INVALID_ODDS_PAYLOAD"
INVALID_BOOKMAKER = "INVALID_BOOKMAKER"
INVALID_MARKET = "INVALID_MARKET"
INVALID_SELECTION = "INVALID_SELECTION"
INVALID_ODDS_VALUE = "INVALID_ODDS_VALUE"
PARTIAL_ODDS_SNAPSHOT = "PARTIAL_ODDS_SNAPSHOT"
ODDS_TRANSACTION_FAILURE = "ODDS_TRANSACTION_FAILURE"
ODDS_FLUSH_FAILURE = "ODDS_FLUSH_FAILURE"
ODDS_ANALYTICS_FAILURE = "ODDS_ANALYTICS_FAILURE"
COMMIT_UNKNOWN = "COMMIT_UNKNOWN"
COMMIT_CONFIRMED = "COMMIT_CONFIRMED"
COMMIT_NOT_CONFIRMED = "COMMIT_NOT_CONFIRMED"
CACHE_INVALIDATION_FAILED = "CACHE_INVALIDATION_FAILED"
SUCCESS = "SUCCESS"
RETRYABLE = "RETRYABLE"
TERMINAL = "TERMINAL"
IDENTITY_BOUNDARY_VIOLATION = "IDENTITY_BOUNDARY_VIOLATION"

RETRYABLE_DEFERRED_STATES = {
    PROVIDER_RATE_LIMITED,
    PROVIDER_REQUEST_FAILED,
    ODDS_FLUSH_FAILURE,
    ODDS_ANALYTICS_FAILURE,
    CACHE_INVALIDATION_FAILED,
}
NON_RETRYABLE_STATES = {
    PROVIDER_FIXTURE_MISMATCH,
    INVALID_ODDS_PAYLOAD,
    INVALID_BOOKMAKER,
    INVALID_MARKET,
    INVALID_SELECTION,
    INVALID_ODDS_VALUE,
    PARTIAL_ODDS_SNAPSHOT,
}


class OddsTransactionFailure(RuntimeError):
    """Failure that must be rolled back by the existing outer session owner."""

    def __init__(self, failure_state: str, message: str, *, cause: Exception | None = None) -> None:
        super().__init__(message)
        self.failure_state = failure_state
        self.cause = cause


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
        self.odds_service = odds_service
        self.cache_service = cache_service or CacheService()
        self.odds_provider = odds_provider or OddsProvider(self.odds_service.client)
        self.odds_repository = odds_repository or OddsRepository()
        self.analytics_projection_service = analytics_projection_service or AnalyticsProjectionService()

    async def verify_commit_unknown(self, db, local_match_id: int, persistence_rows: list[dict]) -> dict:
        """Verify an ambiguous outer commit without retrying the snapshot write."""
        try:
            committed = await self.odds_repository.snapshot_matches_rows(db, local_match_id, persistence_rows)
        except Exception as exc:
            logger.error(
                "ODDS_COMMIT_UNKNOWN",
                extra={
                    "local_match_id": local_match_id,
                    "failure_state": COMMIT_UNKNOWN,
                    "transaction_state": "verification_failed",
                    "exception_type": exc.__class__.__name__,
                },
            )
            return {"state": COMMIT_UNKNOWN}
        return {"state": COMMIT_CONFIRMED if committed else COMMIT_NOT_CONFIRMED}

    @staticmethod
    def is_commit_ambiguous(exc: Exception) -> bool:
        """Identify connection-loss failures whose commit outcome cannot be known."""
        if isinstance(exc, DBAPIError):
            return bool(exc.connection_invalidated)
        return isinstance(exc, (ConnectionError, TimeoutError, OSError))

    @staticmethod
    def classify_retry_state(state: str | None) -> str:
        if state in RETRYABLE_DEFERRED_STATES:
            return "RETRYABLE_DEFERRED"
        if state in NON_RETRYABLE_STATES:
            return "NON_RETRYABLE"
        if state in {COMMIT_UNKNOWN, COMMIT_CONFIRMED}:
            return "AMBIGUOUS" if state == COMMIT_UNKNOWN else "COMMITTED"
        if state == COMMIT_NOT_CONFIRMED:
            return "DEFERRED_COMMIT"
        if state == PROVIDER_QUOTA_EXHAUSTED:
            return "DEFERRED_QUOTA"
        return "NO_RETRY"

    @staticmethod
    def classify_failure_state(reason: str | None) -> str:
        if reason is None:
            return SUCCESS
        normalized = str(reason).strip()
        if not normalized:
            return SUCCESS
        if normalized in {"Match not found", "provider_fixture_id missing", INVALID_PROVIDER, INVALID_PROVIDER_FIXTURE_ID, PROVIDER_FIXTURE_MISMATCH, IDENTITY_BOUNDARY_VIOLATION}:
            return IDENTITY_BOUNDARY_VIOLATION
        if normalized in {
            PROVIDER_RATE_LIMITED,
            PROVIDER_QUOTA_EXHAUSTED,
            PROVIDER_REQUEST_FAILED,
            ODDS_FLUSH_FAILURE,
            ODDS_ANALYTICS_FAILURE,
            CACHE_INVALIDATION_FAILED,
        }:
            return RETRYABLE
        return TERMINAL

    @staticmethod
    def _explicit_provider_classification(result: FootballAPIResponse) -> str | None:
        text = " ".join(value for value in (result.error_code, result.error_message) if value).lower()
        if any(token in text for token in ("rate limit", "rate_limited", "throttl", "too many requests")):
            return PROVIDER_RATE_LIMITED
        if any(token in text for token in ("quota", "daily limit", "monthly limit", "request limit reached")):
            return PROVIDER_QUOTA_EXHAUSTED
        return None

    @classmethod
    def _classify_provider_result(cls, result, provider_fixture_id: int) -> tuple[str | None, dict | None]:
        if isinstance(result, FootballAPIResponse):
            explicit = cls._explicit_provider_classification(result)
            if explicit:
                return explicit, result.payload if isinstance(result.payload, dict) else None
            if result.status_code == 429:
                return PROVIDER_RATE_LIMITED, result.payload if isinstance(result.payload, dict) else None
            if result.status_code is not None and result.status_code >= 400:
                return PROVIDER_REQUEST_FAILED, result.payload if isinstance(result.payload, dict) else None
            if result.exception_type or result.error_message:
                return PROVIDER_REQUEST_FAILED, None
            payload = result.payload
        else:
            payload = result

        if not isinstance(payload, Mapping):
            return INVALID_ODDS_PAYLOAD, None
        if "response" not in payload:
            return None, payload
        if not isinstance(payload.get("response"), list):
            return None, payload
        responses = payload["response"]
        for item in responses:
            if not isinstance(item, Mapping):
                return None, payload
            response_fixture_id = (item.get("fixture") or {}).get("id")
            if response_fixture_id is None:
                return None, payload
            try:
                if int(response_fixture_id) != int(provider_fixture_id):
                    return PROVIDER_FIXTURE_MISMATCH, payload
            except (TypeError, ValueError):
                return None, payload
        return None, payload

    @staticmethod
    def _validate_odds_payload(payload: dict) -> str | None:
        responses = payload.get("response")
        if not isinstance(responses, list):
            return INVALID_ODDS_PAYLOAD

        for item in responses:
            if not isinstance(item, Mapping):
                return INVALID_ODDS_PAYLOAD
            fixture = item.get("fixture")
            if not isinstance(fixture, Mapping) or fixture.get("id") is None:
                return INVALID_ODDS_PAYLOAD
            bookmakers = item.get("bookmakers")
            if not isinstance(bookmakers, list):
                return PARTIAL_ODDS_SNAPSHOT
            for bookmaker in bookmakers:
                if not isinstance(bookmaker, Mapping) or normalize_bookmaker_name(bookmaker.get("name")) != "bet365":
                    continue
                bets = bookmaker.get("bets")
                if not isinstance(bets, list):
                    return PARTIAL_ODDS_SNAPSHOT
                market_ids = set()
                for bet in bets:
                    if not isinstance(bet, Mapping):
                        return INVALID_MARKET
                    market_type = classify_market_id(bet.get("id"))
                    if not market_type:
                        continue
                    if market_type in market_ids:
                        return INVALID_MARKET
                    market_ids.add(market_type)
                    values = bet.get("values")
                    if not isinstance(values, list):
                        return PARTIAL_ODDS_SNAPSHOT
                    for value in values:
                        if not isinstance(value, Mapping):
                            return INVALID_SELECTION
                        selection = value.get("value", value.get("selection"))
                        if not isinstance(selection, str) or not selection.strip():
                            return INVALID_SELECTION
                        try:
                            normalize_odds_value(value.get("odd"))
                        except ValueError:
                            return INVALID_ODDS_VALUE
        return None

    @observe_sync("odds")
    async def refresh_odds(self, db, fixture_id: int, cache_key: str, pre_match_ttl: int) -> dict:
        # Resolve the canonical local Match row first.
        match = (await db.execute(select(Match).where(Match.match_id == fixture_id))).scalar_one_or_none()
        if match is None:
            log_projection_failure("odds", {"match_id": fixture_id}, "match_not_found")
            return {"error": "Match not found"}

        provider_fixture_id = getattr(match, "provider_fixture_id", None)
        provider = str(getattr(match, "provider", "") or "").strip().casefold()
        if provider != "api-football":
            log_projection_failure("odds", {"match_id": fixture_id}, "invalid_provider")
            return {
                "status": IDENTITY_BOUNDARY_VIOLATION,
                "failure_reason": INVALID_PROVIDER,
                "processed": 0,
                "created": 0,
                "updated": 0,
                "unchanged": 0,
                "rejected": 0,
                "error": INVALID_PROVIDER,
            }

        try:
            provider_fixture_id = int(provider_fixture_id)
        except (TypeError, ValueError):
            provider_fixture_id = None

        if provider_fixture_id is None or provider_fixture_id <= 0:
            log_projection_failure("odds", {"match_id": fixture_id}, "provider_fixture_id_missing")
            return {
                "status": IDENTITY_BOUNDARY_VIOLATION,
                "failure_reason": INVALID_PROVIDER_FIXTURE_ID,
                "processed": 0,
                "created": 0,
                "updated": 0,
                "unchanged": 0,
                "rejected": 0,
                "error": INVALID_PROVIDER_FIXTURE_ID,
            }

        # Provider receives the external provider fixture identity only.
        try:
            result = await self.odds_provider.get_match_odds(provider_fixture_id)
        except Exception as exc:
            logger.warning(
                "ODDS_PROVIDER_CLASSIFICATION",
                extra={"local_match_id": fixture_id, "provider_fixture_id": provider_fixture_id, "classification": PROVIDER_REQUEST_FAILED, "exception_type": exc.__class__.__name__},
            )
            return {
                "status": RETRYABLE,
                "failure_reason": PROVIDER_REQUEST_FAILED,
                "processed": 0,
                "created": 0,
                "updated": 0,
                "unchanged": 0,
                "rejected": 0,
                "error": PROVIDER_REQUEST_FAILED,
            }

        classification, payload = self._classify_provider_result(result, provider_fixture_id)
        if classification:
            logger.warning(
                "ODDS_PROVIDER_CLASSIFICATION",
                extra={"local_match_id": fixture_id, "provider_fixture_id": provider_fixture_id, "classification": classification, "provider_http_status": getattr(result, "status_code", None), "provider_error_code": getattr(result, "error_code", None), "provider_error_message": getattr(result, "error_message", None)},
            )
            return {
                "status": self.classify_failure_state(classification),
                "failure_reason": classification,
                "processed": 0,
                "created": 0,
                "updated": 0,
                "unchanged": 0,
                "rejected": 0,
                "error": classification,
            }

        validation_error = self._validate_odds_payload(payload)
        if validation_error:
            logger.warning(
                "ODDS_PAYLOAD_REJECTED",
                extra={
                    "local_match_id": fixture_id,
                    "provider_fixture_id": provider_fixture_id,
                    "validation_state": "INVALID",
                    "validation_reason": validation_error,
                },
            )
            return {
                "status": self.classify_failure_state(validation_error),
                "failure_reason": validation_error,
                "processed": 0,
                "created": 0,
                "updated": 0,
                "unchanged": 0,
                "rejected": 0,
                "error": validation_error,
            }

        selection_metrics: dict[str, int] = {}
        normalized_rows, rejected = normalize_odds_response(
            payload,
            provider_fixture_id,
            fixture_id,
            metrics=selection_metrics,
        )
        logger.info(
            "ODDS_SELECTION_SUMMARY",
            extra={
                "local_match_id": fixture_id,
                "provider_fixture_id": provider_fixture_id,
                **selection_metrics,
            },
        )
        if rejected:
            return {
                "status": TERMINAL,
                "failure_reason": PARTIAL_ODDS_SNAPSHOT,
                "processed": 0,
                "created": 0,
                "updated": 0,
                "unchanged": 0,
                "rejected": len(rejected),
                "error": PARTIAL_ODDS_SNAPSHOT,
                "rejected_records": rejected,
            }

        if not normalized_rows:
            return {
                "status": TERMINAL,
                "failure_reason": PROVIDER_EMPTY,
                "processed": 0,
                "created": 0,
                "updated": 0,
                "unchanged": 0,
                "rejected": len(rejected),
                "error": PROVIDER_EMPTY,
                "rejected_records": rejected,
            }

        now_utc = datetime.now(timezone.utc)
        persistence_rows = []
        for record in normalized_rows:
            persistence_rows.append(
                {
                    "fixture_id": record["fixture_id"],
                    "bookmaker_name": record["bookmaker_name"],
                    "market_name": record["market_name"],
                    "selection": record["selection"],
                    "odd_value": record["odd_value"],
                    "myanmar_odd": record.get("myanmar_odd"),
                    "last_updated": now_utc,
                }
            )

        existing_odds = await self.odds_repository.get_fixture_odds(db, fixture_id)
        existing_by_key = {
            (
                row.bookmaker_name,
                row.market_name,
                row.selection,
            ): row.odd_value for row in existing_odds
        }
        new_by_key = {
            (
                row["bookmaker_name"],
                row["market_name"],
                row["selection"],
            ): row["odd_value"] for row in normalized_rows
        }
        created = sum(1 for key in new_by_key if key not in existing_by_key)
        unchanged = sum(1 for key, value in new_by_key.items() if key in existing_by_key and existing_by_key[key] == value)
        updated = sum(1 for key, value in new_by_key.items() if key in existing_by_key and existing_by_key[key] != value)

        try:
            await self.odds_repository.replace_fixture_odds(db, fixture_id, persistence_rows)
            await db.flush()
        except Exception as exc:
            logger.error(
                "ODDS_TRANSACTION_FAILURE",
                extra={
                    "local_match_id": fixture_id,
                    "provider_fixture_id": provider_fixture_id,
                    "failure_state": ODDS_FLUSH_FAILURE,
                    "transaction_state": "rollback_required",
                },
            )
            raise OddsTransactionFailure(ODDS_FLUSH_FAILURE, "Odds persistence or flush failed", cause=exc) from exc

        try:
            projection = await self.analytics_projection_service.project_odds(db, fixture_id, persistence_rows)
            if not projection["success"]:
                raise ValueError(f"Odds analytics projection rejected: {projection.get('reason', 'reconciliation failure')}")
        except Exception as exc:
            logger.error(
                "ODDS_TRANSACTION_FAILURE",
                extra={
                    "local_match_id": fixture_id,
                    "provider_fixture_id": provider_fixture_id,
                    "failure_state": ODDS_ANALYTICS_FAILURE,
                    "transaction_state": "rollback_required",
                },
            )
            raise OddsTransactionFailure(ODDS_ANALYTICS_FAILURE, "Odds analytics projection failed", cause=exc) from exc
        odds_data = [
            {
                "bookmaker": r["bookmaker_name"],
                "market": r["market_name"],
                "selection": r["selection"],
                "odd": r["odd_value"],
                "myanmar_odd": r.get("myanmar_odd"),
                "updated_at": now_utc.isoformat(),
            }
            for r in normalized_rows
        ]
        refresh_result = {
            "source": "api",
            "odds": odds_data,
            "cached": False,
            "match_started": False,
            "processed": len(normalized_rows),
            "created": created,
            "updated": updated,
            "unchanged": unchanged,
            "rejected": len(rejected),
            "status": SUCCESS,
            "failure_reason": None,
            "analytics": projection,
            "transaction_state": "pending_outer_commit",
            "_transaction_context": {
                "local_match_id": fixture_id,
                "provider_fixture_id": provider_fixture_id,
                "persistence_rows": persistence_rows,
            },
        }
        # The scheduler invalidates this key after its outer transaction commits.
        return refresh_result
