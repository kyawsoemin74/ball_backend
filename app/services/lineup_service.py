from copy import deepcopy
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
from app.services.player_service import PlayerService
from app.services.player_sync_service import PlayerSyncService
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
        player_service: PlayerService | None = None,
    ) -> None:
        self.client = client
        self.cache_service = cache_service or CacheService()
        self.lineup_provider = lineup_provider or LineupProvider(client)
        self.lineup_sync_service = lineup_sync_service or LineupSyncService(
            lineup_provider=self.lineup_provider,
            player_identity_resolution_service=player_identity_resolution_service,
        )
        self.team_service = team_service or TeamService(client=client, cache_service=self.cache_service)
        self.player_service = player_service or PlayerService()

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

        seen_teams: set[str] = set()
        seen_players: set[str] = set()
        for lineup in lineup_data:
            if not isinstance(lineup, dict):
                return False

            team = lineup.get("team")
            if not isinstance(team, dict):
                return False
            team_id = self._normalize_provider_id(team.get("id"))
            if team_id is None or team_id in seen_teams:
                return False
            seen_teams.add(team_id)

            if not isinstance(lineup.get("startXI"), list):
                return False

            if not isinstance(lineup.get("substitutes"), list):
                return False
            formation = lineup.get("formation")
            if formation is not None:
                if not isinstance(formation, str) or not formation.strip():
                    return False
                formation_parts = (
                    [part.strip() for part in formation.split("-")]
                    if "-" in formation
                    else formation.split()
                )
                if not formation_parts or any(
                    not part or not part.isdigit() or int(part) <= 0 for part in formation_parts
                ):
                    return False
            for section in ("startXI", "substitutes"):
                for entry in lineup[section]:
                    if not isinstance(entry, dict) or not isinstance(entry.get("player"), dict):
                        return False
                    player = entry["player"]
                    raw_player_id = player.get("id")
                    if raw_player_id is None or (
                        isinstance(raw_player_id, str) and not raw_player_id.strip()
                    ):
                        raw_player_id = player.get("provider_id")
                    if raw_player_id is None or (
                        isinstance(raw_player_id, str) and not raw_player_id.strip()
                    ):
                        continue

                    player_id = self._normalize_provider_id(raw_player_id)
                    if player_id is None:
                        return False
                    alternate_player_id = player.get("provider_id")
                    if alternate_player_id is not None and not (
                        isinstance(alternate_player_id, str) and not alternate_player_id.strip()
                    ):
                        normalized_alternate_id = self._normalize_provider_id(alternate_player_id)
                        if normalized_alternate_id is None or normalized_alternate_id != player_id:
                            return False
                    if player_id in seen_players:
                        return False
                    seen_players.add(player_id)

        return True

    @staticmethod
    def _normalize_provider_id(value: Any) -> str | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return str(value) if value > 0 else None
        if isinstance(value, str):
            normalized = value.strip()
            if normalized.isdigit() and int(normalized) > 0:
                return str(int(normalized))
        return None

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

    @staticmethod
    def _same_local_player_id(left: Any, right: Any) -> bool:
        try:
            return int(left) == int(right)
        except (TypeError, ValueError):
            return str(left).strip() == str(right).strip()

    @staticmethod
    def _normalize_local_player_id(value: Any) -> int | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value if value > 0 else None
        if isinstance(value, str):
            normalized = value.strip()
            if normalized.isdigit() and int(normalized) > 0:
                return int(normalized)
        return None

    @staticmethod
    def _lineup_provider_player_id(player_data: dict[str, Any]) -> str | None:
        provider_ids = []
        for field in ("provider_player_id", "provider_id"):
            value = player_data.get(field)
            if value is None or (isinstance(value, str) and not value.strip()):
                continue
            if isinstance(value, bool) or not isinstance(value, (str, int)):
                return None
            provider_id = str(value).strip()
            if provider_id:
                provider_ids.append(provider_id)
        if not provider_ids or any(value != provider_ids[0] for value in provider_ids[1:]):
            return None
        return provider_ids[0]

    async def _enrich_player_photos(self, db: AsyncSession, payload: Any, match_id: int) -> Any:
        if not isinstance(payload, list):
            return payload

        enriched_payload = deepcopy(payload)
        photo_entries: list[tuple[dict[str, Any], str | None, int | None, str | None]] = []
        provider_ids: list[str] = []
        for lineup in enriched_payload:
            if not isinstance(lineup, dict):
                continue
            for section in ("startXI", "substitutes"):
                entries = lineup.get(section)
                if not isinstance(entries, list):
                    continue
                for entry in entries:
                    player_data = entry.get("player") if isinstance(entry, dict) else None
                    if not isinstance(player_data, dict):
                        continue
                    provider_id = self._lineup_provider_player_id(player_data)
                    local_player_id = self._normalize_local_player_id(
                        player_data.get("local_player_id")
                    )
                    lineup_photo = PlayerSyncService._normalize_photo_url(
                        player_data.get("photo")
                    )
                    photo_entries.append(
                        (player_data, provider_id, local_player_id, lineup_photo)
                    )
                    if lineup_photo is None and provider_id is not None:
                        provider_ids.append(provider_id)

        provider_ids = list(dict.fromkeys(provider_ids))
        players_by_provider_id: dict[str, Any] = {}
        ambiguous_provider_ids: set[str] = set()
        if provider_ids:
            try:
                players = await self.player_service.get_players_by_provider_ids(db, provider_ids)
            except Exception:
                logger.exception(
                    "LINEUP_PLAYER_PHOTO_BATCH_LOOKUP_FAILED",
                    extra={"match_id": match_id},
                )
                players = []

            for player in players:
                provider_id = str(getattr(player, "provider_id", "") or "").strip()
                if provider_id not in provider_ids:
                    continue
                provider = getattr(player, "provider", None)
                if provider is not None and str(provider).strip().casefold() != "api-football":
                    continue
                if provider_id in players_by_provider_id:
                    ambiguous_provider_ids.add(provider_id)
                else:
                    players_by_provider_id[provider_id] = player
            for provider_id in ambiguous_provider_ids:
                players_by_provider_id.pop(provider_id, None)

        local_ids = list(dict.fromkeys(
            local_player_id
            for _, provider_id, local_player_id, lineup_photo in photo_entries
            if lineup_photo is None
            and provider_id is not None
            and local_player_id is not None
            and provider_id not in players_by_provider_id
            and provider_id not in ambiguous_provider_ids
        ))
        players_by_local_id: dict[int, Any] = {}
        ambiguous_local_ids: set[int] = set()
        if local_ids:
            try:
                local_players = await self.player_service.get_players_by_ids(db, local_ids)
            except Exception:
                logger.exception(
                    "LINEUP_PLAYER_PHOTO_LOCAL_BATCH_LOOKUP_FAILED",
                    extra={"match_id": match_id},
                )
                local_players = []

            for player in local_players:
                local_player_id = self._normalize_local_player_id(
                    getattr(player, "player_id", None)
                )
                if local_player_id not in local_ids:
                    continue
                if local_player_id in players_by_local_id:
                    ambiguous_local_ids.add(local_player_id)
                else:
                    players_by_local_id[local_player_id] = player
            for local_player_id in ambiguous_local_ids:
                players_by_local_id.pop(local_player_id, None)

        for player_data, provider_id, local_player_id, lineup_photo in photo_entries:
            selected_photo = lineup_photo
            if selected_photo is None and provider_id is not None:
                player = None
                provider_player = (
                    players_by_provider_id.get(provider_id)
                    if provider_id not in ambiguous_provider_ids
                    else None
                )
                if provider_player is not None:
                    if (
                        local_player_id is None
                        or self._same_local_player_id(
                            local_player_id,
                            getattr(provider_player, "player_id", None),
                        )
                    ):
                        player = provider_player
                elif (
                    local_player_id is not None
                    and provider_id not in ambiguous_provider_ids
                    and local_player_id not in ambiguous_local_ids
                ):
                    local_player = players_by_local_id.get(local_player_id)
                    if (
                        local_player is not None
                        and self._same_local_player_id(
                            local_player_id,
                            getattr(local_player, "player_id", None),
                        )
                        and str(getattr(local_player, "provider", "")).strip().casefold()
                        == "api-football"
                        and str(getattr(local_player, "provider_id", "")).strip()
                        == provider_id
                    ):
                        player = local_player

                selected_photo = (
                    PlayerSyncService._normalize_photo_url(
                        getattr(player, "photo", None)
                    )
                    if player is not None
                    else None
                )

            if selected_photo is not None:
                player_data["photo"] = selected_photo
            else:
                player_data.pop("photo", None)

        return enriched_payload

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
                canonical = self._canonical_response(match_id, payload)
                return await self._enrich_player_photos(db, canonical, match_id)

        logger.debug("LINEUP_CACHE_MISS", extra={"match_id": match_id})
        db_record = (await db.execute(select(MatchLineup).where(MatchLineup.match_id == match_id))).scalar_one_or_none()
        if db_record:
            try:
                await self.cache_service.set_json(cache_key, self._cache_payload(db_record), settings.REDIS_TTL_LINEUP)
            except Exception:
                logger.exception("LINEUP_CACHE_WRITE_FAILED", extra={"match_id": match_id})
            logger.debug("LINEUP_CACHE_SET", extra={"match_id": match_id})
            canonical = self._canonical_response(match_id, db_record.data)
            return await self._enrich_player_photos(db, canonical, match_id)

        return None
