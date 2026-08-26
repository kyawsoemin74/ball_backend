import logging
import re
import unicodedata
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.providers.coach_provider import CoachProvider
from app.repositories.coach_repository import CoachRepository
from app.services.base.football_client import FootballAPIClient

logger = logging.getLogger(__name__)


class CoachSyncService:
    """Sync orchestration for Coach Master.

    Current API-Football source truth:
      /coachs response list includes a coach record with id/name/nationality/photo.

    Production-safe behavior:
      - canonical identity is local coach_id
      - provider is always "api-football"
      - provider_id is nullable when absent, but API-Football does expose numeric id
      - matching uses deterministic name normalization, not fuzzy name guessing
    """

    def __init__(self, repository: CoachRepository | None = None) -> None:
        self.client = FootballAPIClient()
        self.provider = CoachProvider(self.client)
        self.repository = repository or CoachRepository()

    @staticmethod
    def normalize_coach_name(name: Any) -> dict:
        raw = str(name or "").strip()
        if not raw:
            raise ValueError("coach name is required")

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

    async def sync_team_coach(self, db: AsyncSession | None, payload: dict | None) -> dict:
        if not isinstance(payload, dict):
            raise ValueError("coach payload is required")

        coach_id = payload.get("id")
        name = payload.get("name")
        if not name or not str(name).strip():
            raise ValueError("coach name is required")

        normalized = self.normalize_coach_name(name)
        normalized["provider_id"] = str(coach_id) if coach_id is not None else None
        normalized["nationality"] = payload.get("nationality")
        normalized["photo"] = payload.get("photo")

        existing = None
        if db is not None:
            if normalized["provider_id"]:
                existing = await self.repository.get_by_provider_id(db, normalized["provider_id"], "api-football")
            if existing is None:
                existing = await self.repository.get_by_normalized_name(db, normalized["normalized_name"], "api-football")

        if existing is not None:
            existing_id = getattr(existing, "coach_id", None)
            if existing_id is None and isinstance(existing, dict):
                existing_id = existing.get("coach_id")
            update_values = {}
            for key, value in normalized.items():
                if key in {"provider", "provider_id", "normalized_name"}:
                    continue
                if value is not None:
                    update_values[key] = value
            if update_values and existing_id is not None and db is not None:
                existing = await self.repository.update(db, existing_id, update_values)

            existing_provider = getattr(existing, "provider", None)
            if existing_provider is None and isinstance(existing, dict):
                existing_provider = existing.get("provider")
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
                "coach_id": existing_id,
                "provider": existing_provider,
                "provider_id": existing_provider_id,
                "name": existing_name,
                "normalized_name": existing_normalized_name,
                "nationality": existing_nationality,
                "photo": existing_photo,
            }

        if db is None:
            return {
                "coach_id": None,
                "provider": normalized["provider"],
                "provider_id": normalized["provider_id"],
                "name": normalized["name"],
                "normalized_name": normalized["normalized_name"],
                "nationality": normalized["nationality"],
                "photo": normalized["photo"],
            }

        row = await self.repository.create(db, {
            "provider": normalized["provider"],
            "provider_id": normalized["provider_id"],
            "name": normalized["name"],
            "normalized_name": normalized["normalized_name"],
            "nationality": normalized["nationality"],
            "photo": normalized["photo"],
        })
        return {
            "coach_id": row.coach_id,
            "provider": row.provider,
            "provider_id": row.provider_id,
            "name": row.name,
            "normalized_name": row.normalized_name,
            "nationality": row.nationality,
            "photo": row.photo,
        }

    async def ensure_coaches_exist(self, db: AsyncSession, coach_payloads: list[dict]) -> dict:
        """Idempotent synchronization of multiple coach payloads from provider responses."""
        if not coach_payloads:
            return {"success": True, "created": 0, "updated": 0, "skipped": 0, "errors": []}

        created = 0
        updated = 0
        skipped = 0
        errors = []
        seen = set()

        for payload in coach_payloads:
            if not isinstance(payload, dict):
                skipped += 1
                continue
            name = payload.get("name")
            if not isinstance(name, str) or not name.strip():
                skipped += 1
                continue
            key = name.strip().casefold()
            if key in seen:
                continue
            seen.add(key)
            try:
                candidate = self.normalize_coach_name(name)
                candidate["provider_id"] = str(payload.get("id")) if payload.get("id") is not None else None
                candidate["nationality"] = payload.get("nationality")
                candidate["photo"] = payload.get("photo")
                existing = None
                if candidate["provider_id"]:
                    existing = await self.repository.get_by_provider_id(db, candidate["provider_id"], "api-football")
                if existing is None:
                    existing = await self.repository.get_by_normalized_name(db, candidate["normalized_name"], "api-football")
                if existing is not None:
                    updated += 1
                    continue
                record = await self.sync_team_coach(db, payload)
                if record.get("coach_id") is None:
                    skipped += 1
                    continue
                created += 1
            except Exception as exc:  # pragma: no cover - defensive path
                logger.error("Error syncing coach %s: %s", name, exc)
                errors.append(str(exc))

        return {"success": not errors, "created": created, "updated": updated, "skipped": skipped, "errors": errors}
