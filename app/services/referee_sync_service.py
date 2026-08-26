import logging
import re
import unicodedata
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.providers.referee_provider import RefereeProvider
from app.repositories.referee_repository import RefereeRepository
from app.services.base.football_client import FootballAPIClient

logger = logging.getLogger(__name__)


class RefereeSyncService:
    """Sync orchestration for Referee Master.

    Current API-Football source truth:
      fixture.referee = "Referee Name" string

    Production-safe behavior:
      - canonical identity is local referee_id
      - provider is always "api-football"
      - provider_id is nullable and remains NULL unless a real provider id is later exposed
      - matching uses deterministic name normalization, not fuzzy name guessing
    """

    def __init__(self, repository: RefereeRepository | None = None) -> None:
        self.client = FootballAPIClient()
        self.provider = RefereeProvider(self.client)
        self.repository = repository or RefereeRepository()

    @staticmethod
    def normalize_referee_name(name: Any) -> dict:
        raw = str(name or "").strip()
        if not raw:
            raise ValueError("referee name is required")

        collapsed = re.sub(r"\s+", " ", raw)
        ascii_value = unicodedata.normalize("NFKD", collapsed)
        ascii_value = "".join(ch for ch in ascii_value if not unicodedata.combining(ch))
        normalized_name = ascii_value.casefold().strip()

        return {
            "provider": "api-football",
            "provider_id": None,
            "name": collapsed,
            "normalized_name": normalized_name,
            "nationality": None,
            "photo": None,
        }

    async def sync_fixture_referee(self, db: AsyncSession | None, referee_name: str | None) -> dict:
        if not referee_name or not str(referee_name).strip():
            raise ValueError("referee_name is required")

        normalized = self.normalize_referee_name(referee_name)
        existing = None
        if db is not None:
            existing = await self.repository.get_by_normalized_name(db, normalized["normalized_name"], "api-football")

        if existing is not None:
            existing_id = getattr(existing, "referee_id", None)
            if existing_id is None:
                existing_id = existing.get("referee_id")
            update_values = {}
            for key, value in normalized.items():
                if key in {"provider", "provider_id", "normalized_name"}:
                    continue
                if value is not None:
                    update_values[key] = value
            if update_values and existing_id is not None:
                existing = await self.repository.update(db, existing_id, update_values)

            existing_provider = getattr(existing, "provider", None)
            if existing_provider is None:
                existing_provider = existing.get("provider") if isinstance(existing, dict) else None
            existing_provider_id = getattr(existing, "provider_id", None)
            if existing_provider_id is None and isinstance(existing, dict):
                existing_provider_id = existing.get("provider_id")
            existing_name = getattr(existing, "name", None)
            if existing_name is None and isinstance(existing, dict):
                existing_name = existing.get("name")
            existing_normalized_name = getattr(existing, "normalized_name", None)
            if existing_normalized_name is None and isinstance(existing, dict):
                existing_normalized_name = existing.get("normalized_name")
            existing_nationality = getattr(existing, "nationality", None)
            if existing_nationality is None and isinstance(existing, dict):
                existing_nationality = existing.get("nationality")
            existing_photo = getattr(existing, "photo", None)
            if existing_photo is None and isinstance(existing, dict):
                existing_photo = existing.get("photo")
            return {
                "referee_id": existing_id,
                "provider": existing_provider,
                "provider_id": existing_provider_id,
                "name": existing_name,
                "normalized_name": existing_normalized_name,
                "nationality": existing_nationality,
                "photo": existing_photo,
            }

        if db is None:
            return {
                "referee_id": None,
                "provider": normalized["provider"],
                "provider_id": normalized["provider_id"],
                "name": normalized["name"],
                "normalized_name": normalized["normalized_name"],
                "nationality": normalized["nationality"],
                "photo": normalized["photo"],
            }

        referee_data = {
            "provider": normalized["provider"],
            "provider_id": normalized["provider_id"],
            "name": normalized["name"],
            "normalized_name": normalized["normalized_name"],
            "nationality": normalized["nationality"],
            "photo": normalized["photo"],
        }
        try:
            if hasattr(db, "begin_nested"):
                async with db.begin_nested():
                    row = await self.repository.create(db, referee_data)
            else:
                row = await self.repository.create(db, referee_data)
        except IntegrityError:
            logger.warning(
                "REFEREE_UNIQUE_CONFLICT_RETRY",
                extra={"provider": normalized["provider"], "normalized_name": normalized["normalized_name"]},
            )
            existing = await self.repository.get_by_normalized_name(
                db,
                normalized["normalized_name"],
                normalized["provider"],
            )
            if existing is None:
                raise
            row = existing
        return {
            "referee_id": row.referee_id,
            "provider": row.provider,
            "provider_id": row.provider_id,
            "name": row.name,
            "normalized_name": row.normalized_name,
            "nationality": row.nationality,
            "photo": row.photo,
        }

    async def ensure_referees_exist(self, db: AsyncSession, referee_names: list[str]) -> dict:
        """Idempotent synchronization of multiple referee names from fixture payloads."""
        if not referee_names:
            return {"success": True, "created": 0, "updated": 0, "skipped": 0, "errors": []}

        created = 0
        updated = 0
        skipped = 0
        errors = []

        seen = set()
        for referee_name in referee_names:
            if not isinstance(referee_name, str) or not referee_name.strip():
                skipped += 1
                continue
            candidate = referee_name.strip()
            if not candidate:
                skipped += 1
                continue
            key = candidate.casefold()
            if key in seen:
                continue
            seen.add(key)
            try:
                existing = await self.repository.get_by_normalized_name(db, self.normalize_referee_name(candidate)["normalized_name"], "api-football")
                if existing is not None:
                    updated += 1
                    continue
                record = await self.sync_fixture_referee(db, candidate)
                if record.get("referee_id") is None:
                    skipped += 1
                    continue
                created += 1
            except Exception as exc:  # pragma: no cover - defensive path
                logger.error("Error syncing referee %s: %s", referee_name, exc)
                errors.append(str(exc))

        return {"success": not errors, "created": created, "updated": updated, "skipped": skipped, "errors": errors}
