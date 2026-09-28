from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.league_season_repository import LeagueSeasonRepository
from app.services.season_identity import normalize_season


class LeagueSeasonSyncService:
    def __init__(self, repository: LeagueSeasonRepository | None = None):
        self.repository = repository or LeagueSeasonRepository()

    async def normalize_season_value(self, value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, str) and not value.strip():
            return None
        return normalize_season(value)

    @staticmethod
    def _parse_provider_date(value: Any) -> datetime | None:
        if value is None or value == "":
            return None
        if not isinstance(value, str):
            raise ValueError("Season date must be an ISO string")
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed

    @staticmethod
    def _same_datetime(left: datetime | None, right: datetime | None) -> bool:
        if left is None or right is None:
            return left is None and right is None
        if left.tzinfo is None:
            left = left.replace(tzinfo=timezone.utc)
        if right.tzinfo is None:
            right = right.replace(tzinfo=timezone.utc)
        return left.astimezone(timezone.utc) == right.astimezone(timezone.utc)

    async def sync_league_seasons(
        self,
        db: AsyncSession,
        *,
        league_id: int,
        seasons: list[dict] | None,
    ) -> dict:
        provider_seasons = seasons or []
        if not isinstance(provider_seasons, list):
            raise ValueError("Seasons payload must be a list")

        normalized_rows = []
        seen_seasons = set()
        current_count = 0
        for season_data in provider_seasons:
            if not isinstance(season_data, dict):
                raise ValueError("Season payload must be an object")

            try:
                season_text = await self.normalize_season_value(season_data.get("year"))
            except ValueError as exc:
                raise ValueError("Season year must be a positive integer") from exc
            if season_text is None:
                raise ValueError("Season year is required")

            start_date = self._parse_provider_date(season_data.get("start"))
            end_date = self._parse_provider_date(season_data.get("end"))
            current = season_data.get("current")
            if current is not None and not isinstance(current, bool):
                raise ValueError("Season current must be a boolean")
            if current is True:
                current_count += 1

            if season_text in seen_seasons:
                raise ValueError(f"Duplicate provider season: {season_text}")
            seen_seasons.add(season_text)
            provider_season_id = season_data.get("id")
            normalized_rows.append(
                {
                    "league_id": int(league_id),
                    "season": season_text,
                    "provider": "api-football",
                    "provider_id": str(provider_season_id) if provider_season_id is not None else None,
                    "start_date": start_date,
                    "end_date": end_date,
                    "current": current,
                }
            )

        if current_count > 1:
            raise ValueError("Multiple current seasons found")

        existing_rows = await self.repository.list_by_league(db, int(league_id))
        existing_by_season = {str(row.season): row for row in existing_rows}
        result = {
            "success": True,
            "league_id": int(league_id),
            "inserted": 0,
            "updated": 0,
            "no_op": 0,
            "seasons": [],
        }

        for row in normalized_rows:
            existing = existing_by_season.get(row["season"])
            if existing is None:
                action = "PLANNED_INSERT"
                result["inserted"] += 1
                await self.repository.upsert_one(db, row)
            else:
                metadata_matches = (
                    existing.provider == row["provider"]
                    and existing.provider_id == row["provider_id"]
                    and self._same_datetime(existing.start_date, row["start_date"])
                    and self._same_datetime(existing.end_date, row["end_date"])
                    and existing.current == row["current"]
                )
                if metadata_matches:
                    action = "PLANNED_NO_OP"
                    result["no_op"] += 1
                else:
                    action = "PLANNED_UPDATE"
                    result["updated"] += 1
                    await self.repository.upsert_one(db, row)

            result["seasons"].append(
                {
                    "season": row["season"],
                    "action": action,
                    "provider": row["provider"],
                    "provider_id": row["provider_id"],
                    "start_date": row["start_date"],
                    "end_date": row["end_date"],
                    "current": row["current"],
                }
            )

        return result

    async def upsert_season(self, db: AsyncSession, *, league_id: int, season: Any, provider: str | None = None, provider_id: str | None = None, start_date=None, end_date=None, current: bool | None = None) -> dict:
        season_text = await self.normalize_season_value(season)
        if season_text is None:
            raise ValueError("Season value is required")

        row = {
            "league_id": int(league_id),
            "season": season_text,
            "provider": provider,
            "provider_id": provider_id,
            "start_date": start_date,
            "end_date": end_date,
            "current": current,
        }
        record = await self.repository.upsert_one(db, row)
        return {
            "success": True,
            "id": record.id,
            "league_id": record.league_id,
            "season": record.season,
            "provider": record.provider,
            "provider_id": record.provider_id,
            "current": record.current,
        }
