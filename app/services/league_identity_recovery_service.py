import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.cache import make_cache_key
from app.db import async_session
from app.models.league import League
from app.providers.league_provider import LeagueProvider
from app.repositories.league_identity_recovery_repository import LeagueIdentityRecoveryRepository
from app.repositories.league_repository import LeagueRepository
from app.services.cache_service import CacheService
from app.services.resource_lock import run_with_resource_lock

logger = logging.getLogger(__name__)

PROVIDER = "api-football"
MAX_ATTEMPTS = 5
RETRY_DELAYS_MINUTES = (15, 30, 60, 120, 240)
RETRYABLE_CODES = {"NO_CANDIDATE", "PROVIDER_TIMEOUT", "PROVIDER_UNAVAILABLE", "PROVIDER_ERROR"}


def _normalize(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).casefold()


class LeagueIdentityRecoveryService:
    def __init__(
        self,
        league_provider: LeagueProvider,
        league_repository: LeagueRepository | None = None,
        recovery_repository: LeagueIdentityRecoveryRepository | None = None,
        cache_service: CacheService | None = None,
    ) -> None:
        self.league_provider = league_provider
        self.league_repository = league_repository or LeagueRepository()
        self.recovery_repository = recovery_repository or LeagueIdentityRecoveryRepository()
        self.cache_service = cache_service or CacheService()

    @staticmethod
    def _candidate_parts(item: dict) -> tuple[dict, dict]:
        league = item.get("league") if isinstance(item.get("league"), dict) else item
        country = item.get("country") if isinstance(item.get("country"), dict) else {}
        return league if isinstance(league, dict) else {}, country

    @classmethod
    def select_candidate(cls, league: League, payload: object) -> tuple[str, str | None]:
        if not isinstance(payload, dict) or not isinstance(payload.get("response"), list):
            return "INVALID_PROVIDER_RESPONSE", None

        local_name = _normalize(league.name)
        local_country = _normalize(league.country)
        if not local_name or not local_country:
            return "LOCAL_METADATA_INSUFFICIENT", None

        candidates = []
        for item in payload["response"]:
            if not isinstance(item, dict):
                continue
            provider_league, provider_country = cls._candidate_parts(item)
            provider_id = provider_league.get("id")
            provider_name = _normalize(provider_league.get("name"))
            provider_country_name = _normalize(provider_country.get("name") or provider_league.get("country"))
            provider_country_code = _normalize(provider_country.get("code") or provider_league.get("country_code"))
            local_country_code = _normalize(getattr(league, "country_code", None))
            country_matches = provider_country_name == local_country or (
                local_country_code and provider_country_code == local_country_code
            )
            if provider_id and provider_name == local_name and country_matches:
                candidates.append(str(provider_id))

        if not candidates:
            return "NO_CANDIDATE", None
        if len(set(candidates)) != 1:
            return "AMBIGUOUS_PROVIDER_MATCH", None
        provider_id = candidates[0]
        if not provider_id.isdigit() or int(provider_id) <= 0:
            return "INVALID_PROVIDER_ID", None
        return "RESOLVED", provider_id

    @staticmethod
    def _retry_at(attempt_count: int, now: datetime) -> datetime:
        index = min(max(attempt_count - 1, 0), len(RETRY_DELAYS_MINUTES) - 1)
        return now + timedelta(minutes=RETRY_DELAYS_MINUTES[index])

    @staticmethod
    def _failure_class(code: str) -> str:
        return "IDENTITY_RESOLUTION_RETRY" if code in RETRYABLE_CODES else "IDENTITY_RESOLUTION_FAILED"

    async def recover(self, league_id: int) -> dict:
        async with async_session() as db:
            locked, result = await run_with_resource_lock(
                db,
                "league_identity",
                league_id,
                lambda: self._recover_locked(db, int(league_id)),
            )
            if not locked:
                return {
                    "success": False,
                    "state": "IDENTITY_RESOLUTION_RETRY",
                    "error_code": "LEAGUE_IDENTITY_LOCKED",
                    "league_id": int(league_id),
                    "retryable": True,
                }
            return result

    async def _recover_locked(self, db: AsyncSession, league_id: int) -> dict:
        league = await self.league_repository.get_by_id(db, league_id)
        if league is None:
            await db.rollback()
            return {
                "success": False,
                "state": "IDENTITY_RESOLUTION_FAILED",
                "error_code": "LEAGUE_NOT_FOUND",
                "league_id": league_id,
                "retryable": False,
            }

        if str(league.provider or "").strip() == PROVIDER and str(league.provider_id or "").strip().isdigit():
            resolved_provider_id = str(league.provider_id)
            await db.rollback()
            return {
                "success": True,
                "state": "IDENTITY_RESOLVED",
                "league_id": league_id,
                "provider_id": resolved_provider_id,
                "already_resolved": True,
            }

        record = await self.recovery_repository.ensure_required(db, league_id)
        now = datetime.now(timezone.utc)
        if record.attempt_count >= MAX_ATTEMPTS and record.state == "IDENTITY_RESOLUTION_FAILED":
            await db.rollback()
            return {
                "success": False,
                "state": record.state,
                "error_code": record.last_error_code or "MAX_ATTEMPTS_EXCEEDED",
                "league_id": league_id,
                "retryable": False,
            }

        await self.recovery_repository.mark_running(db, record, now)
        try:
            payload = await self.league_provider.get_leagues_by_name(str(league.name))
        except TimeoutError:
            payload = None
            code = "PROVIDER_TIMEOUT"
        except Exception as exc:
            payload = None
            code = "PROVIDER_ERROR"
            logger.warning("LEAGUE_IDENTITY_PROVIDER_LOOKUP_FAILED league_id=%s error=%s", league_id, exc)
        else:
            code, provider_id = self.select_candidate(league, payload)

        if code == "RESOLVED" and provider_id is not None:
            try:
                existing = await self.league_repository.find_by_provider_identity(db, PROVIDER, provider_id)
                if existing is not None and int(existing.league_id) != league_id:
                    raise ValueError("PROVIDER_IDENTITY_CONFLICT")
                await self.league_repository.attach_provider_identity(db, league_id, PROVIDER, provider_id)
                persisted = await self.league_repository.find_by_provider_identity(db, PROVIDER, provider_id)
                if persisted is None or int(persisted.league_id) != league_id:
                    raise ValueError("IDENTITY_RERESOLUTION_FAILED")
                await self.recovery_repository.mark_resolved(db, record, PROVIDER, provider_id, now)
                await db.commit()
                try:
                    await self.cache_service.delete(make_cache_key("league", league_id))
                    await self.cache_service.delete(make_cache_key("leagues_grouped"))
                except Exception:
                    logger.exception("LEAGUE_IDENTITY_CACHE_INVALIDATION_FAILED league_id=%s", league_id)
                return {
                    "success": True,
                    "state": "IDENTITY_RESOLVED",
                    "league_id": league_id,
                    "provider_id": provider_id,
                }
            except Exception as exc:
                await db.rollback()
                code = "IDENTITY_CONFLICT" if str(exc) == "PROVIDER_IDENTITY_CONFLICT" else "DATABASE_FAILURE"

        retryable = code in RETRYABLE_CODES
        terminal = not retryable or record.attempt_count >= MAX_ATTEMPTS
        state = "IDENTITY_RESOLUTION_FAILED" if terminal else "IDENTITY_RESOLUTION_RETRY"
        next_retry = None if terminal else self._retry_at(record.attempt_count, now)
        await self.recovery_repository.mark_failure(
            db,
            record,
            state=state,
            error_code=code,
            message=code,
            next_retry_at=next_retry,
        )
        await db.commit()
        return {
            "success": False,
            "state": state,
            "error_code": code,
            "league_id": league_id,
            "retryable": retryable and not terminal,
            "attempt_count": record.attempt_count,
            "next_retry_at": next_retry.isoformat() if next_retry else None,
        }

    async def recover_due(self, db: AsyncSession, now: datetime, limit: int = 100) -> list[dict]:
        candidates = await self.recovery_repository.get_retry_candidates(db, now, limit)
        results = []
        for candidate in candidates:
            results.append(await self.recover(int(candidate.league_id)))
        return results
