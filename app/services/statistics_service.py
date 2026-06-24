import logging
import re
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.match import Match
from app.services.base.football_client import FootballAPIClient
from app.services.cache_service import CacheService

logger = logging.getLogger(__name__)


class StatisticsService:
    _FINISHED_STATUSES = {"FT", "AET", "PEN"}
    _FINISHED_TTL_SECONDS = 172800
    _LIVE_TTL_SECONDS = 600

    _STAT_LABEL_OVERRIDES = {
        "ball possession": "Ball Possession",
        "shots on goal": "Shots on Goal",
        "shots off goal": "Shots off Goal",
        "total shots": "Total Shots",
        "blocked shots": "Blocked Shots",
        "fouls": "Fouls",
        "corner kicks": "Corner Kicks",
        "offsides": "Offsides",
        "yellow cards": "Yellow Cards",
        "red cards": "Red Cards",
        "goalkeeper saves": "Goalkeeper Saves",
        "expected goals": "Expected Goals",
        "expected assists": "Expected Assists",
        "big chances created": "Big Chances Created",
        "hit woodwork": "Hit Woodwork",
        "free kicks": "Free Kicks",
        "throw ins": "Throw Ins",
        "goal kicks": "Goal Kicks",
        "dangerous attacks": "Dangerous Attacks",
    }

    def __init__(self, client: FootballAPIClient, cache_service: CacheService | None = None) -> None:
        self.client = client
        self.cache_service = cache_service or CacheService()

    @staticmethod
    def _normalize_data_name(raw_name: str) -> str:
        text = re.sub(r"[^a-z0-9]+", "_", raw_name.lower()).strip("_")
        return text or "statistic"

    @staticmethod
    def _normalize_stat_value(value: Any) -> Any:
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return None

            if text.endswith("%"):
                return text

            try:
                if "." in text:
                    return float(text)
                return int(text)
            except ValueError:
                return text

        return value

    @classmethod
    def _label_for_stat(cls, raw_name: str) -> str:
        text = raw_name.strip().lower()
        return cls._STAT_LABEL_OVERRIDES.get(text, raw_name.strip() or "Statistic")

    def _normalize_statistics_payload(
        self,
        raw_payload: dict,
        match_id: int,
        home_team_id: Optional[int] = None,
        away_team_id: Optional[int] = None,
    ) -> dict:
        response = raw_payload.get("response", []) if isinstance(raw_payload, dict) else []
        if not isinstance(response, list) or not response:
            return {"match_id": match_id, "statistics": []}

        entries = []
        for item in response:
            if isinstance(item, dict):
                entries.append(item)

        if len(entries) < 2:
            return {"match_id": match_id, "statistics": []}

        mapped_entries: dict[str, dict[str, Any]] = {}

        for index, item in enumerate(entries):
            team = item.get("team") if isinstance(item.get("team"), dict) else {}
            team_id = team.get("id")

            if home_team_id is not None and team_id == home_team_id:
                mapped_entries["home"] = item
            elif away_team_id is not None and team_id == away_team_id:
                mapped_entries["away"] = item
            elif index == 0:
                mapped_entries.setdefault("home", item)
            elif index == 1:
                mapped_entries.setdefault("away", item)

        home_entry = mapped_entries.get("home", entries[0] if isinstance(entries[0], dict) else {})
        away_entry = mapped_entries.get("away", entries[1] if len(entries) > 1 and isinstance(entries[1], dict) else {})

        home_stats = home_entry.get("statistics", []) if isinstance(home_entry.get("statistics"), list) else []
        away_stats = away_entry.get("statistics", []) if isinstance(away_entry.get("statistics"), list) else []

        stat_map: dict[str, dict[str, Any]] = {}

        for stat in home_stats:
            if not isinstance(stat, dict):
                continue
            raw_name = str(stat.get("type") or stat.get("name") or "").strip()
            if not raw_name:
                continue
            data_name = self._normalize_data_name(raw_name)
            stat_map.setdefault(data_name, {
                "data_name": data_name,
                "label": self._label_for_stat(raw_name),
                "home_value": None,
                "away_value": None,
            })
            stat_map[data_name]["home_value"] = self._normalize_stat_value(stat.get("value"))

        for stat in away_stats:
            if not isinstance(stat, dict):
                continue
            raw_name = str(stat.get("type") or stat.get("name") or "").strip()
            if not raw_name:
                continue
            data_name = self._normalize_data_name(raw_name)
            stat_map.setdefault(data_name, {
                "data_name": data_name,
                "label": self._label_for_stat(raw_name),
                "home_value": None,
                "away_value": None,
            })
            stat_map[data_name]["away_value"] = self._normalize_stat_value(stat.get("value"))

        return {
            "match_id": match_id,
            "statistics": list(stat_map.values()),
        }

    async def get_match_statistics(self, match_id: int) -> Optional[dict]:
        return await self.client.get("/fixtures/statistics", params={"fixture": match_id})

    async def sync_match_statistics(self, db: AsyncSession, match_id: int) -> dict:
        api_res = await self.get_match_statistics(match_id)
        if not api_res or "response" not in api_res:
            return {"success": False, "message": "Statistics not found"}

        statistics_data = api_res.get("response")
        if not statistics_data:
            return {"success": False, "message": "Statistics not found"}

        return {"success": True, "match_id": match_id}

    async def get_cached_statistics(self, db: AsyncSession, match_id: int) -> dict:
        from app.cache import make_cache_key

        cache_key = make_cache_key("match", match_id, "statistics")
        cached = await self.cache_service.get_json(cache_key)
        if cached is not None:
            return cached

        api_res = await self.get_match_statistics(match_id)
        if not api_res or "response" not in api_res:
            return {"error": "Statistics not found"}

        statistics_data = api_res.get("response")
        if not statistics_data:
            return {"error": "Statistics not found"}

        match = (await db.execute(select(Match).where(Match.match_id == match_id))).scalar_one_or_none()
        status = (getattr(match, "status", None) or "").upper()

        if status in self._FINISHED_STATUSES:
            ttl = self._FINISHED_TTL_SECONDS
            logger.info(
                "STATISTICS_CACHE_SET_FT",
                extra={"match_id": match_id, "status": status, "ttl": ttl},
            )
        else:
            ttl = self._LIVE_TTL_SECONDS
            logger.info(
                "STATISTICS_CACHE_SET_LIVE",
                extra={"match_id": match_id, "status": status, "ttl": ttl},
            )

        await self.cache_service.set_json(cache_key, api_res, ttl)
        return api_res

    async def get_normalized_statistics(self, db: AsyncSession, match_id: int) -> dict:
        raw_payload = await self.get_cached_statistics(db, match_id)
        if not raw_payload or "error" in raw_payload:
            return {"error": "Statistics not found"}

        match = (await db.execute(select(Match).where(Match.match_id == match_id))).scalar_one_or_none()
        home_team_id = getattr(match, "home_team_id", None) if match else None
        away_team_id = getattr(match, "away_team_id", None) if match else None

        return self._normalize_statistics_payload(raw_payload, match_id, home_team_id, away_team_id)
