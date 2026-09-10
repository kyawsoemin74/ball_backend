import inspect
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache import make_cache_key
from app.core.config import settings
from app.models.match_lineup import MatchLineup
from app.providers.lineup_provider import LineupProvider
from app.services.base.football_client import FootballAPIClient
from app.services.cache_service import CacheService
from app.services.lineup_sync_service import LineupSyncService
from app.services.player_identity_resolution_service import PlayerIdentityResolutionService
from app.services.team_service import TeamService

logger = logging.getLogger(__name__)


def make_lineup_cache_key(match_id: int) -> str:
    return make_cache_key("lineup", match_id)


class LineupService:
    def __init__(
        self,
        client: FootballAPIClient,
        cache_service: object | None = None,
        lineup_provider: LineupProvider | None = None,
        lineup_sync_service: LineupSyncService | None = None,
        team_service: TeamService | None = None,
        player_identity_resolution_service: PlayerIdentityResolutionService | None = None,
    ) -> None:
        self.client = client
        self.cache_service = cache_service or CacheService()
        self.lineup_provider = lineup_provider or LineupProvider(client)
        self.lineup_sync_service = lineup_sync_service or LineupSyncService(
            lineup_provider=self.lineup_provider,
            player_identity_resolution_service=player_identity_resolution_service,
        )
        self.team_service = team_service or TeamService(client=client, cache_service=self.cache_service)

    @staticmethod
    def _cache_payload_and_timestamp(cached: Any) -> tuple[Any, datetime | None]:
        if isinstance(cached, dict) and isinstance(cached.get("data"), list):
            updated_at_value = cached.get("updated_at")
            try:
                updated_at = LineupService._parse_timestamp(updated_at_value)
            except Exception:
                updated_at = None
            return cached.get("data"), updated_at
        return cached, None

    @staticmethod
    def _parse_timestamp(value: Any) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            return value
        if isinstance(value, str):
            normalized = value.replace("Z", "+00:00")
            try:
                parsed = datetime.fromisoformat(normalized)
            except Exception:
                parsed = None
            if parsed is not None and parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed
        return None

    @staticmethod
    def _cache_payload(record: MatchLineup) -> dict[str, Any]:
        updated_at = getattr(record, "updated_at", None)
        return {
            "data": record.data,
            "updated_at": updated_at.isoformat() if isinstance(updated_at, datetime) else str(updated_at) if updated_at else None,
        }

    async def _is_cached_lineup_stale(self, db: AsyncSession, match_id: int, cached: Any) -> bool:
        if not isinstance(cached, dict) or not isinstance(cached.get("data"), list):
            return False
        updated_at_value = cached.get("updated_at")
        cache_updated_at = self._parse_timestamp(updated_at_value)
        if cache_updated_at is None:
            return False

        db_record = (await db.execute(select(MatchLineup).where(MatchLineup.match_id == match_id))).scalar_one_or_none()
        if db_record is None:
            return False
        db_updated_at = getattr(db_record, "updated_at", None)
        if not isinstance(db_updated_at, datetime):
            return False
        return db_updated_at.tzinfo is not None and db_updated_at > cache_updated_at

    def _is_valid_lineup_response(self, lineup_data: Any) -> bool:
        if not isinstance(lineup_data, list) or not lineup_data:
            return False

        seen_players: set[tuple[Any, str]] = set()
        for lineup in lineup_data:
            if not isinstance(lineup, dict):
                return False

            team = lineup.get("team")
            if not isinstance(team, dict) or not team.get("id"):
                return False

            if not isinstance(lineup.get("startXI"), list):
                return False

            if not isinstance(lineup.get("substitutes"), list):
                return False
            formation = lineup.get("formation")
            if formation is not None and (not isinstance(formation, str) or not formation.strip()):
                return False
            for section, role in (("startXI", "STARTER"), ("substitutes", "SUBSTITUTE")):
                for entry in lineup[section]:
                    if not isinstance(entry, dict) or not isinstance(entry.get("player"), dict):
                        return False
                    player_id = entry["player"].get("id")
                    if player_id is None or str(player_id).strip() == "":
                        return False
                    key = str(player_id)
                    if key in seen_players:
                        return False
                    seen_players.add(key)

        return True

    @staticmethod
    def _canonical_response(match_id: int, payload: Any) -> Any:
        if not isinstance(payload, list):
            return payload
        response = []
        for lineup in payload:
            if not isinstance(lineup, dict):
                continue
            item = dict(lineup)
            item["local_match_id"] = int(match_id)
            provider_team = dict(item.get("team") or {})
            local_team_id = item.pop("local_team_id", None)
            if local_team_id is not None:
                provider_team_id = provider_team.pop("id", None)
                item["team"] = {
                    "local_team_id": int(local_team_id),
                    "provider_team_id": str(provider_team_id) if provider_team_id is not None else None,
                    **provider_team,
                }
            for section in ("startXI", "substitutes"):
                entries = []
                for entry in item.get(section, []):
                    entry_copy = dict(entry)
                    player = dict(entry_copy.get("player") or {})
                    local_player_id = player.pop("player_id", None)
                    provider_player_id = player.pop("id", None)
                    if local_player_id is not None:
                        entry_copy["player"] = {
                            "local_player_id": int(local_player_id),
                            "provider_player_id": str(provider_player_id) if provider_player_id is not None else None,
                            **player,
                        }
                    entries.append(entry_copy)
                item[section] = entries
            response.append(item)
        return response

    async def get_match_lineup(self, match_id: int, db: AsyncSession | None = None) -> Optional[List[Dict[str, Any]]]:
        """Compatibility read path: cache first, then DB fallback, never provider fetch."""
        cache_key = make_lineup_cache_key(match_id)
        try:
            cached = await self.cache_service.get_json(cache_key)
        except Exception:
            logger.exception("LINEUP_CACHE_READ_FAILED", extra={"match_id": match_id})
            cached = None
        if cached is not None:
            payload, _ = self._cache_payload_and_timestamp(cached)
            stale = False
            if db is not None and isinstance(cached, dict) and isinstance(cached.get("data"), list):
                stale = await self._is_cached_lineup_stale(db, match_id, cached)
            if stale:
                logger.warning("LINEUP_CACHE_STALE", extra={"match_id": match_id, "cache_key": cache_key})
                cached = None
            else:
                logger.debug("LINEUP_CACHE_HIT", extra={"match_id": match_id})
                return self._canonical_response(match_id, payload)

        logger.debug("LINEUP_CACHE_MISS", extra={"match_id": match_id})
        if db is None:
            return None

        db_record = (await db.execute(select(MatchLineup).where(MatchLineup.match_id == match_id))).scalar_one_or_none()
        if db_record:
            try:
                await self.cache_service.set_json(cache_key, self._cache_payload(db_record), settings.REDIS_TTL_LINEUP)
            except Exception:
                logger.exception("LINEUP_CACHE_WRITE_FAILED", extra={"match_id": match_id})
            logger.debug("LINEUP_CACHE_SET", extra={"match_id": match_id})
            return self._canonical_response(match_id, db_record.data)

        return None

    async def sync_lineup(
        self,
        db: AsyncSession,
        match_id: int,
        *,
        allow_terminal_status: bool = False,
        invalidate_cache: bool = False,
    ) -> Dict[str, Any]:
        cache_key = make_lineup_cache_key(match_id)
        sync_kwargs = {
            "db": db,
            "match_id": match_id,
            "validate_lineup": self._is_valid_lineup_response,
            "cache_service": self.cache_service,
            "cache_key": cache_key,
        }
        if allow_terminal_status:
            sync_kwargs["allow_terminal_status"] = True
        sync_signature = inspect.signature(self.lineup_sync_service.sync_lineup)
        if "invalidate_cache" in sync_signature.parameters:
            sync_kwargs["invalidate_cache"] = invalidate_cache
        sync_result = await self.lineup_sync_service.sync_lineup(
            **sync_kwargs,
        )
        return sync_result

    async def get_cached_match_lineup(self, db: AsyncSession, match_id: int) -> Optional[List[Dict[str, Any]]]:
        cache_key = make_lineup_cache_key(match_id)
        try:
            cached = await self.cache_service.get_json(cache_key)
        except Exception:
            logger.exception("LINEUP_CACHE_READ_FAILED", extra={"match_id": match_id})
            cached = None
        if cached is not None:
            payload, _ = self._cache_payload_and_timestamp(cached)
            stale = await self._is_cached_lineup_stale(db, match_id, cached)
            if stale:
                logger.warning("LINEUP_CACHE_STALE", extra={"match_id": match_id, "cache_key": cache_key})
                cached = None
            else:
                logger.debug("LINEUP_CACHE_HIT", extra={"match_id": match_id})
                return self._canonical_response(match_id, payload)

        logger.debug("LINEUP_CACHE_MISS", extra={"match_id": match_id})
        db_record = (await db.execute(select(MatchLineup).where(MatchLineup.match_id == match_id))).scalar_one_or_none()
        if db_record:
            try:
                await self.cache_service.set_json(cache_key, self._cache_payload(db_record), settings.REDIS_TTL_LINEUP)
            except Exception:
                logger.exception("LINEUP_CACHE_WRITE_FAILED", extra={"match_id": match_id})
            logger.debug("LINEUP_CACHE_SET", extra={"match_id": match_id})
            return self._canonical_response(match_id, db_record.data)

        return None
