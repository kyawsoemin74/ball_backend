import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

from app.repositories.match_repository import MatchRepository
from app.repositories.match_finalization_repository import MatchFinalizationRepository

logger = logging.getLogger(__name__)

FINAL_MATCH_TERMINAL_STATUSES = frozenset({"FT", "AET", "PEN"})
FINAL_MATCH_WAIT_STATUSES = frozenset({"1H", "2H", "HT", "ET", "LIVE", "BT", "P"})
PROVIDER_NAME = "api-football"


@dataclass(frozen=True)
class FinalMatchState:
    status: str
    elapsed: int
    home_score: int
    away_score: int


class FinalMatchSyncService:
    """Fetch and stage an authoritative terminal Match snapshot only."""

    def __init__(
        self,
        fixture_provider,
        *,
        match_repository: MatchRepository | None = None,
        finalization_repository: MatchFinalizationRepository | None = None,
    ) -> None:
        self.fixture_provider = fixture_provider
        self.match_repository = match_repository or MatchRepository()
        self.finalization_repository = (
            finalization_repository or MatchFinalizationRepository()
        )

    async def sync_final_match(self, db, match_id: int) -> dict:
        record = await self.finalization_repository.get_by_match_id(
            db, match_id, for_update=True
        )
        if record is None:
            return {
                "state": "EXHAUSTED",
                "category": "FINALIZATION_STATE_MISSING",
                "message": "No durable finalization state exists for the Match.",
                "retryable": False,
            }
        if record.state == "SUCCESS":
            return {"state": "SUCCESS", "already_finalized": True}
        if record.state != "RUNNING":
            return {
                "state": record.state,
                "category": "ATTEMPT_NOT_CLAIMED",
                "message": "Finalization attempt was not claimed.",
                "retryable": False,
            }

        match = await self.match_repository.get_by_id(db, int(match_id))
        if match is None:
            return {
                "state": "EXHAUSTED",
                "category": "MATCH_NOT_FOUND",
                "message": "Canonical Match identity no longer exists.",
                "retryable": False,
            }
        provider_fixture_id = getattr(match, "provider_fixture_id", None)
        if (
            str(getattr(match, "provider", "")).casefold() != PROVIDER_NAME
            or isinstance(provider_fixture_id, bool)
            or not isinstance(provider_fixture_id, int)
            or provider_fixture_id <= 0
        ):
            return {
                "state": "EXHAUSTED",
                "category": "INVALID_MATCH_IDENTITY",
                "message": "Canonical Match has an invalid API-Football identity.",
                "retryable": False,
            }

        try:
            response = await self.fixture_provider.get_fixtures_by_ids(
                [str(provider_fixture_id)]
            )
        except (httpx.HTTPError, TimeoutError, ConnectionError, OSError) as exc:
            logger.warning(
                "FINAL_MATCH_SYNC_PROVIDER_RETRYABLE match_id=%s error=%s",
                match_id,
                type(exc).__name__,
            )
            return {
                "state": "FAILED",
                "category": "PROVIDER_FAILURE",
                "message": str(exc),
                "retryable": True,
            }

        if response is None:
            return {
                "state": "FAILED",
                "category": "PROVIDER_FAILURE",
                "message": "Fixture-by-ID provider request returned no response.",
                "retryable": True,
            }
        if not isinstance(response, dict) or not isinstance(response.get("response"), list):
            return {
                "state": "EXHAUSTED",
                "category": "INVALID_PROVIDER_RESPONSE",
                "message": "Fixture-by-ID response has an invalid structure.",
                "retryable": False,
            }

        fixtures = response["response"]
        matching_fixtures = [
            item
            for item in fixtures
            if isinstance(item, dict)
            and isinstance(item.get("fixture"), dict)
            and str(item["fixture"].get("id")) == str(provider_fixture_id)
        ]
        if len(matching_fixtures) > 1:
            return {
                "state": "EXHAUSTED",
                "category": "INVALID_PROVIDER_RESPONSE",
                "message": "Fixture-by-ID response contains duplicate fixture identities.",
                "retryable": False,
            }
        fixture = matching_fixtures[0] if matching_fixtures else None
        if fixture is None:
            return {
                "state": "FAILED",
                "category": "PROVIDER_FIXTURE_MISSING",
                "message": "Fixture-by-ID response did not contain the requested fixture.",
                "retryable": True,
            }

        fixture_info = fixture.get("fixture")
        status_payload = fixture_info.get("status") if isinstance(fixture_info, dict) else None
        status = str(status_payload.get("short") or "").strip().upper() if isinstance(status_payload, dict) else ""
        if status not in FINAL_MATCH_TERMINAL_STATUSES:
            if status in FINAL_MATCH_WAIT_STATUSES:
                logger.info(
                    "FINAL_MATCH_SYNC_WAITING_FOR_TERMINAL match_id=%s status=%s",
                    match_id,
                    status,
                )
                return {
                    "state": "WAITING",
                    "status": status,
                    "message": f"Fixture-by-ID is still active with status {status}.",
                }
            return {
                "state": "EXHAUSTED",
                "category": "PROVIDER_STATUS_NOT_TERMINAL",
                "message": f"Fixture-by-ID returned non-terminal status {status or 'UNKNOWN'}.",
                "retryable": False,
            }

        goals = fixture.get("goals")
        elapsed = status_payload.get("elapsed") if isinstance(status_payload, dict) else None
        final_state = self._validated_state(status, elapsed, goals)
        if final_state is None:
            return {
                "state": "EXHAUSTED",
                "category": "INVALID_FINAL_MATCH_STATE",
                "message": "Terminal provider fixture is missing valid elapsed or score values.",
                "retryable": False,
            }

        updated = await self.match_repository.update_live_state(
            db,
            int(match_id),
            status=final_state.status,
            elapsed=final_state.elapsed,
            home_score=final_state.home_score,
            away_score=final_state.away_score,
        )
        if not updated:
            return {
                "state": "EXHAUSTED",
                "category": "MATCH_NOT_FOUND",
                "message": "Canonical Match disappeared before final state persistence.",
                "retryable": False,
            }

        await self.finalization_repository.mark_success(
            db, record, completed_at=datetime.now(timezone.utc)
        )
        return {
            "state": "SUCCESS",
            "final_status": final_state.status,
            "elapsed": final_state.elapsed,
            "home_score": final_state.home_score,
            "away_score": final_state.away_score,
        }

    @staticmethod
    def _validated_state(status: str, elapsed, goals) -> FinalMatchState | None:
        if (
            isinstance(elapsed, bool)
            or not isinstance(elapsed, int)
            or not isinstance(goals, dict)
        ):
            return None
        home_score = goals.get("home")
        away_score = goals.get("away")
        if any(
            isinstance(score, bool) or not isinstance(score, int) or score < 0
            for score in (home_score, away_score)
        ):
            return None
        if elapsed < 0:
            return None
        return FinalMatchState(status, elapsed, home_score, away_score)
