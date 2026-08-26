import hashlib
import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.country_repository import CountryRepository

logger = logging.getLogger(__name__)


class CountrySyncService:
    """Synchronizes verified country values from provider payloads into the countries master table."""

    def __init__(self, country_repository: CountryRepository | None = None) -> None:
        self.country_repository = country_repository or CountryRepository()

    @staticmethod
    def _stable_country_id(name: str) -> int:
        digest = hashlib.md5(str(name).strip().lower().encode("utf-8")).hexdigest()
        return int(digest[:8], 16) % 2_147_483_647

    @classmethod
    def normalize_country_payload(cls, payload: Any, *, source: str = "league") -> dict:
        if payload is None:
            raise ValueError(f"{source} country payload is empty")

        if isinstance(payload, str):
            raw_name = payload
            raw_code = None
            raw_flag = None
        elif isinstance(payload, dict):
            raw_name = (
                payload.get("name")
                or payload.get("country")
                or payload.get("country_name")
                or payload.get("title")
            )
            raw_code = payload.get("code") or payload.get("country_code")
            raw_flag = payload.get("flag") or payload.get("logo") or payload.get("image")
        else:
            raise ValueError(f"Unsupported {source} country payload type: {type(payload).__name__}")

        name = str(raw_name).strip() if raw_name is not None else ""
        code = str(raw_code).strip() if raw_code is not None and str(raw_code).strip() else None
        flag = str(raw_flag).strip() if raw_flag is not None and str(raw_flag).strip() else None

        if not name:
            raise ValueError(f"{source} country payload is missing a valid name")

        return {
            "name": name,
            "code": code,
            "flag": flag,
            "source": source,
        }

    async def sync_country(self, db: AsyncSession, payload: Any, *, source: str = "league") -> dict:
        try:
            normalized = self.normalize_country_payload(payload, source=source)
        except ValueError as exc:
            logger.warning("Rejected invalid %s country payload: %s", source, exc)
            return {"status": "invalid", "source": source, "reason": str(exc), "country": None}

        if db is None or not hasattr(db, "execute"):
            return {
                "status": "skipped",
                "source": source,
                "reason": "No active database session available",
                "country": {"name": normalized["name"], "code": normalized["code"], "flag": normalized["flag"]},
            }

        name = normalized["name"]
        row = {
            "country_id": self._stable_country_id(name),
            "name": name,
            "code": normalized["code"],
            "flag": normalized["flag"],
        }

        existing = await self.country_repository.get_by_name(db, name)
        country = await self.country_repository.upsert_one(db, row)

        if not hasattr(country, "country_id"):
            country = {
                "country_id": row.get("country_id"),
                "name": normalized["name"],
                "code": normalized["code"],
                "flag": normalized["flag"],
            }

        if existing is None:
            status = "created"
        elif (getattr(existing, "code", None) or None) != (getattr(country, "code", None) or None) or (getattr(existing, "flag", None) or None) != (getattr(country, "flag", None) or None):
            status = "updated"
        else:
            status = "unchanged"

        country_payload = {
            "country_id": getattr(country, "country_id", row.get("country_id")),
            "name": getattr(country, "name", normalized["name"]),
            "code": getattr(country, "code", normalized["code"]),
            "flag": getattr(country, "flag", normalized["flag"]),
        }

        return {
            "status": status,
            "source": source,
            "country": country_payload,
        }

    async def sync_from_league_payload(self, db: AsyncSession, league_data: dict) -> dict:
        league_payload = league_data.get("league") or league_data
        country_payload = league_data.get("country")
        if isinstance(country_payload, dict):
            normalized = self.normalize_country_payload({
                "name": country_payload.get("name") or league_payload.get("country"),
                "code": country_payload.get("code"),
                "flag": country_payload.get("flag") or league_payload.get("flag"),
            }, source="league")
            return await self.sync_country(db, normalized, source="league")

        if country_payload is not None:
            return await self.sync_country(db, country_payload, source="league")

        league_country = league_payload.get("country")
        if league_country is not None:
            return await self.sync_country(db, league_country, source="league")

        return {"status": "skipped", "source": "league", "reason": "No country value present", "country": None}

    async def sync_from_team_payload(self, db: AsyncSession, team_data: dict) -> dict:
        team_payload = team_data.get("team") or team_data
        country_value = team_payload.get("country")
        if country_value is None:
            return {"status": "skipped", "source": "team", "reason": "No country value present", "country": None}
        return await self.sync_country(db, country_value, source="team")

    async def backfill_legacy_records(self, db: AsyncSession, legacy_rows: list[dict]) -> dict:
        totals = {"created": 0, "updated": 0, "unchanged": 0, "skipped": 0, "invalid": 0, "unresolved": 0}
        seen_names: set[str] = set()

        for row in legacy_rows:
            if not isinstance(row, dict):
                totals["invalid"] += 1
                continue

            country_value = row.get("country") or row.get("country_name") or row.get("country_code")
            if country_value is None:
                totals["skipped"] += 1
                continue

            try:
                normalized = self.normalize_country_payload(country_value, source="legacy")
            except ValueError:
                totals["invalid"] += 1
                continue

            if normalized["name"] in seen_names:
                totals["unresolved"] += 1
                continue
            seen_names.add(normalized["name"])

            result = await self.sync_country(db, normalized, source="legacy")
            status = result.get("status", "skipped")
            if status in totals:
                totals[status] += 1
            else:
                totals["skipped"] += 1

        return totals
