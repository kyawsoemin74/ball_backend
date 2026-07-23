import copy
import logging
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
    ) -> None:
        self.client = client
        self.cache_service = cache_service or CacheService()
        self.lineup_provider = lineup_provider or LineupProvider(client)
        self.lineup_sync_service = lineup_sync_service or LineupSyncService(lineup_provider=self.lineup_provider)
        self.team_service = team_service or TeamService(client=client, cache_service=self.cache_service)

    def _is_valid_lineup_response(self, lineup_data: Any) -> bool:
        if not isinstance(lineup_data, list) or not lineup_data:
            return False

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

        return True

    def _build_player_photo_map(self, squad_payload: Any) -> Dict[int, Optional[str]]:
        if not isinstance(squad_payload, dict):
            return {}

        players = squad_payload.get("players") if isinstance(squad_payload.get("players"), list) else []
        photo_map: Dict[int, Optional[str]] = {}
        for player in players:
            if not isinstance(player, dict):
                continue
            player_id = player.get("player_id")
            if player_id is None:
                continue
            try:
                photo_map[int(player_id)] = player.get("photo")
            except (TypeError, ValueError):
                photo_map[str(player_id)] = player.get("photo")
        return photo_map

    async def _enrich_lineup_with_photos(self, lineup_payload: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not isinstance(lineup_payload, list):
            return lineup_payload

        team_ids: set[int] = set()
        for lineup in lineup_payload:
            if not isinstance(lineup, dict):
                continue
            team = lineup.get("team")
            if not isinstance(team, dict):
                continue
            team_id = team.get("id")
            if team_id is None:
                continue
            try:
                team_key = int(team_id)
            except (TypeError, ValueError):
                continue
            for section_key in ("startXI", "substitutes"):
                players = lineup.get(section_key)
                if not isinstance(players, list) or not players:
                    continue
                team_ids.add(team_key)
                break

        if not team_ids:
            return lineup_payload

        photo_maps: Dict[int, Dict[int, Optional[str]]] = {}
        for team_id in team_ids:
            try:
                squad_payload = await self.team_service.get_cached_team_squad(team_id)
            except Exception:
                return lineup_payload
            photo_maps[team_id] = self._build_player_photo_map(squad_payload)

        enriched_payload = copy.deepcopy(lineup_payload)
        for lineup in enriched_payload:
            if not isinstance(lineup, dict):
                continue
            team = lineup.get("team")
            if not isinstance(team, dict):
                continue
            team_id = team.get("id")
            if team_id is None:
                continue
            try:
                team_key = int(team_id)
            except (TypeError, ValueError):
                continue
            photo_map = photo_maps.get(team_key, {})

            for section_key in ("startXI", "substitutes"):
                players = lineup.get(section_key)
                if not isinstance(players, list):
                    continue
                for player_entry in players:
                    if not isinstance(player_entry, dict):
                        continue
                    player_data = player_entry.get("player")
                    if not isinstance(player_data, dict):
                        continue
                    player_id = player_data.get("id")
                    if player_id is None:
                        player_data["photo"] = None
                        continue
                    try:
                        player_key = int(player_id)
                    except (TypeError, ValueError):
                        player_key = player_id
                    player_data["photo"] = photo_map.get(player_key)

        return enriched_payload

    async def get_match_lineup(self, match_id: int) -> Optional[dict]:
        return None

    async def sync_lineup(self, db: AsyncSession, match_id: int) -> Dict[str, Any]:
        cache_key = make_lineup_cache_key(match_id)
        sync_result = await self.lineup_sync_service.sync_lineup(
            db=db,
            match_id=match_id,
            validate_lineup=self._is_valid_lineup_response,
            cache_service=self.cache_service,
            cache_key=cache_key,
        )
        if sync_result.get("success") and not sync_result.get("skipped"):
            await self.cache_service.delete(cache_key)
            logger.debug("LINEUP_CACHE_DELETE", extra={"match_id": match_id})
        return sync_result

    async def get_cached_match_lineup(self, db: AsyncSession, match_id: int) -> Optional[List[Dict[str, Any]]]:
        cache_key = make_lineup_cache_key(match_id)
        cached = await self.cache_service.get_json(cache_key)
        if cached is not None:
            logger.debug("LINEUP_CACHE_HIT", extra={"match_id": match_id})
            return await self._enrich_lineup_with_photos(cached)

        logger.debug("LINEUP_CACHE_MISS", extra={"match_id": match_id})
        db_record = (await db.execute(select(MatchLineup).where(MatchLineup.match_id == match_id))).scalar_one_or_none()
        if db_record:
            await self.cache_service.set_json(cache_key, db_record.data, settings.REDIS_TTL_LINEUP)
            logger.debug("LINEUP_CACHE_SET", extra={"match_id": match_id})
            return await self._enrich_lineup_with_photos(db_record.data)

        return None
