import logging
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

from sqlalchemy.exc import MultipleResultsFound
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.player import Player
from app.providers.player_provider import PlayerProvider
from app.repositories.player_repository import PlayerRepository
from app.services.player_identity_resolution_service import PlayerIdentityResolutionService
from app.services.player_team_membership_service import PlayerTeamMembershipService

logger = logging.getLogger(__name__)


class PlayerIdentityResolutionError(RuntimeError):
    """Raised when a provider player cannot be assigned to an exact safe identity."""


class PlayerSyncService:
    """
    Write orchestration for Player Master.
    
    Responsibilities:
    - Normalize provider payload to Player data contract
    - Resolve player identity
    - Call PlayerRepository
    - Transaction orchestration
    
    Does NOT:
    - Call external API directly (uses PlayerProvider)
    - Perform raw SQL (uses PlayerRepository)
    """

    def __init__(
        self,
        player_provider: Optional[PlayerProvider] = None,
        player_repository: Optional[PlayerRepository] = None,
        membership_service: Optional[PlayerTeamMembershipService] = None,
        player_identity_resolution_service: Optional[PlayerIdentityResolutionService] = None,
        client: Any | None = None,
    ) -> None:
        if player_provider is not None:
            self.player_provider = player_provider
            self.client = client or getattr(player_provider, "client", None)
        else:
            from app.services.base.football_client import FootballAPIClient

            self.client = client or FootballAPIClient()
            self.player_provider = PlayerProvider(self.client)
        self.player_repository = player_repository or PlayerRepository()
        self.player_identity_resolution_service = player_identity_resolution_service or PlayerIdentityResolutionService(
            player_repository=self.player_repository,
        )
        self.membership_service = membership_service or PlayerTeamMembershipService(
            player_repository=self.player_repository,
        )

    def normalize_squad_player(self, squad_player: dict) -> dict:
        """
        Normalize a player from /players/squads endpoint.
        
        Source payload structure:
        {
            "id": 10080,
            "name": "Fábio",
            "age": 45,
            "nationality": "Brazil",
            "position": "Goalkeeper",
            "number": 1,
            "photo": "https://..."
        }
        """
        if not isinstance(squad_player, dict):
            return {}

        player_id = squad_player.get("id")
        if not player_id:
            return {}

        return {
            "provider": "api-football",
            "provider_id": str(player_id),
            "name": squad_player.get("name") or "",
            "nationality": squad_player.get("nationality"),
            "position": squad_player.get("position"),
            "photo": squad_player.get("photo"),
            "height": None,
            "weight": None,
            "preferred_foot": None,
            "birth_date": None,
            "birth_place": None,
            "birth_country": None,
            "first_name": None,
            "last_name": None,
        }

    def normalize_lineup_player(self, lineup_player_entry: dict) -> dict:
        """
        Normalize a player from /fixtures/lineups endpoint.
        
        Source payload structure:
        {
            "player": {
                "id": 10080,
                "name": "Fabio",
                "number": 1,
                "pos": "G",
                "grid": "1:1",
                "photo": None
            }
        }
        """
        if not isinstance(lineup_player_entry, dict):
            return {}

        player_data = lineup_player_entry.get("player", {})
        if not isinstance(player_data, dict):
            return {}

        player_id = player_data.get("id")
        if not player_id:
            return {}

        return {
            "provider": "api-football",
            "provider_id": str(player_id),
            "name": player_data.get("name") or "",
            "position": None,  # Lineup has pos (code), not full position
            "photo": player_data.get("photo"),
            "nationality": None,
            "height": None,
            "weight": None,
            "preferred_foot": None,
            "birth_date": None,
            "birth_place": None,
            "birth_country": None,
            "first_name": None,
            "last_name": None,
        }

    def normalize_event_player(self, event: dict) -> Optional[dict]:
        """
        Normalize a player from /fixtures/events endpoint.
        
        Source payload structure:
        {
            "player": {
                "id": 10017,
                "name": "Ignacio"
            },
            "assist": {
                "id": 123,
                "name": "Some Player"
            }
        }
        """
        if not isinstance(event, dict):
            return None

        player_data = event.get("player")
        if not isinstance(player_data, dict):
            return None

        player_id = player_data.get("id")
        if not player_id:
            return None

        return {
            "provider": "api-football",
            "provider_id": str(player_id),
            "name": player_data.get("name") or "",
            "position": None,
            "photo": None,
            "nationality": None,
            "height": None,
            "weight": None,
            "preferred_foot": None,
            "birth_date": None,
            "birth_place": None,
            "birth_country": None,
            "first_name": None,
            "last_name": None,
        }

    def normalize_event_assist(self, event: dict) -> Optional[dict]:
        """Extract assist player from event."""
        if not isinstance(event, dict):
            return None

        assist_data = event.get("assist")
        if not isinstance(assist_data, dict):
            return None

        player_id = assist_data.get("id")
        if not player_id:
            return None

        return {
            "provider": "api-football",
            "provider_id": str(player_id),
            "name": assist_data.get("name") or "",
            "position": None,
            "photo": None,
            "nationality": None,
            "height": None,
            "weight": None,
            "preferred_foot": None,
            "birth_date": None,
            "birth_place": None,
            "birth_country": None,
            "first_name": None,
            "last_name": None,
        }

    async def ensure_players_exist(
        self, db: AsyncSession, players_data: List[dict]
    ) -> Dict[str, Any]:
        """
        Ensure a list of players exist in the database.
        Idempotent: safe to call multiple times.
        
        Args:
            db: AsyncSession
            players_data: List of normalized player dicts
            
        Returns:
            {
                "success": bool,
                "created": int,
                "updated": int,
                "skipped": int,
                "errors": [...]
            }
        """
        if not players_data:
            return {"success": True, "created": 0, "updated": 0, "skipped": 0, "errors": []}

        created = 0
        updated = 0
        skipped = 0
        errors = []

        for player_data in players_data:
            try:
                provider_id = player_data.get("provider_id")
                if not provider_id:
                    skipped += 1
                    continue

                existing = await self.player_repository.get_by_provider_id(
                    db, str(provider_id), str(player_data.get("provider") or "api-football")
                )
                await self.upsert_player(db, player_data)
                if existing:
                    updated += 1
                else:
                    created += 1

            except Exception as e:
                logger.error(f"Error syncing player {player_data.get('provider_id')}: {e}")
                errors.append(str(e))

        return {
            "success": len(errors) == 0,
            "created": created,
            "updated": updated,
            "skipped": skipped,
            "errors": errors,
        }

    @staticmethod
    def _normalize_photo_url(value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        photo = value.strip()
        if not photo or any(character.isspace() for character in photo):
            return None
        try:
            parsed = urlsplit(photo)
            if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname:
                return None
            _ = parsed.port
        except ValueError:
            return None
        return photo

    async def sync_squad_player_photo(
        self,
        db: AsyncSession,
        squad_player: dict,
    ) -> dict[str, str]:
        """Persist only a valid Squad photo for an exact provider player identity."""
        normalized = self.normalize_squad_player(squad_player)
        if not normalized:
            return {"status": "skipped", "reason": "invalid_player"}

        provider = str(normalized.get("provider") or "").strip().casefold()
        provider_id = str(normalized.get("provider_id") or "").strip()
        if not provider or not provider_id.isdigit() or int(provider_id) <= 0:
            return {"status": "skipped", "reason": "invalid_player"}

        photo = self._normalize_photo_url(normalized.get("photo"))
        try:
            existing = await self.player_repository.get_by_provider_id(
                db,
                provider_id,
                provider,
            )
        except MultipleResultsFound as exc:
            raise PlayerIdentityResolutionError(
                "Multiple Player Master records share the provider identity"
            ) from exc
        if photo is None:
            reason = (
                "invalid_photo"
                if normalized.get("photo") not in (None, "")
                else "missing_photo"
            )
            if existing is not None and self._normalize_photo_url(existing.photo):
                return {"status": "preserved", "reason": reason}
            return {"status": "skipped", "reason": reason}

        try:
            resolution = await self.player_identity_resolution_service.resolve_provider_player_identity(
                db,
                provider,
                provider_id,
                player_data={**normalized, "photo": photo},
            )
        except MultipleResultsFound as exc:
            raise PlayerIdentityResolutionError(
                "Multiple Player Master records share the provider identity"
            ) from exc
        if resolution.status == "RESOLVED_EXISTING":
            try:
                existing_player_id = int(existing.player_id) if existing is not None else None
                resolved_player_id = int(resolution.local_player_id)
            except (TypeError, ValueError) as exc:
                raise PlayerIdentityResolutionError(
                    "Player identity could not be resolved safely"
                ) from exc
            if (
                resolution.evidence != "provider+provider_id exact match"
                or existing is None
                or str(getattr(existing, "provider", "")).strip().casefold() != provider
                or str(getattr(existing, "provider_id", "")).strip() != provider_id
                or existing_player_id != resolved_player_id
            ):
                raise PlayerIdentityResolutionError(
                    "Player identity was not resolved by exact provider identity"
                )
        elif resolution.status == "CREATE_NEW":
            if existing is not None:
                raise PlayerIdentityResolutionError(
                    "Player identity resolution conflicts with an existing provider identity"
                )
        else:
            raise PlayerIdentityResolutionError(
                "Player identity could not be resolved safely"
            )

        name = str(normalized.get("name") or "").strip()
        if existing is None and not name:
            return {"status": "skipped", "reason": "invalid_player"}
        if existing is not None:
            name = name or str(getattr(existing, "name", "") or "").strip()
            if self._normalize_photo_url(existing.photo) == photo:
                return {"status": "preserved", "reason": "photo_unchanged"}

        _, outcome = await self.player_repository.upsert_squad_photo(
            db,
            provider=provider,
            provider_id=provider_id,
            name=name,
            photo=photo,
        )
        return {"status": outcome, "reason": ""}

    async def upsert_player(
        self,
        db: AsyncSession,
        player_data: dict,
        *,
        expected_local_player_id: int | None = None,
    ) -> Player:
        """
        Upsert a single player by (provider, provider_id) through the shared resolver.
        """
        if not isinstance(player_data, dict):
            raise ValueError("player payload must be a dict")

        provider = str(player_data.get("provider") or "api-football").strip().casefold()
        provider_id = str(player_data.get("provider_id") or "").strip()
        if not provider or not provider_id:
            raise ValueError("provider_id is required for provider-backed player creation")

        normalized_player_data = {
            **player_data,
            "provider": provider,
            "provider_id": provider_id,
        }
        for field in (
            "name",
            "first_name",
            "last_name",
            "nationality",
            "birth_place",
            "birth_country",
            "preferred_foot",
            "position",
        ):
            value = normalized_player_data.get(field)
            if isinstance(value, str):
                normalized_player_data[field] = value.strip() or None
        for field in ("birth_date", "height", "weight"):
            value = normalized_player_data.get(field)
            if isinstance(value, str) and not value.strip():
                normalized_player_data[field] = None

        position = normalized_player_data.get("position")
        if isinstance(position, str) and position.upper() in {"G", "D", "M", "F"}:
            normalized_player_data["position"] = None
        if "photo" in normalized_player_data:
            normalized_player_data["photo"] = self._normalize_photo_url(
                normalized_player_data.get("photo")
            )

        if expected_local_player_id is not None:
            if (
                isinstance(expected_local_player_id, bool)
                or not isinstance(expected_local_player_id, int)
                or expected_local_player_id <= 0
            ):
                raise ValueError("expected local player identity is invalid")

        resolution = await self.player_identity_resolution_service.resolve_provider_player_identity(
            db,
            provider,
            str(provider_id),
            player_data=normalized_player_data,
        )

        if resolution.status == "RESOLVED_EXISTING":
            existing = await self.player_repository.get_by_provider_id(db, str(provider_id), provider)
            if existing is None:
                raise RuntimeError(f"Resolver reported existing player but none was found for provider={provider} provider_id={provider_id}")
            try:
                existing_player_id = int(existing.player_id)
                resolved_player_id = int(resolution.local_player_id)
            except (TypeError, ValueError) as exc:
                raise RuntimeError(
                    f"Resolved player identity conflict for provider={provider} provider_id={provider_id}"
                ) from exc
            if (
                getattr(existing, "provider", None) != provider
                or str(getattr(existing, "provider_id", "")).strip() != provider_id
                or existing_player_id != resolved_player_id
                or (
                    expected_local_player_id is not None
                    and existing_player_id != expected_local_player_id
                )
            ):
                raise RuntimeError(
                    f"Resolved player identity conflict for provider={provider} provider_id={provider_id}"
                )
            if normalized_player_data.get("name") is None:
                normalized_player_data["name"] = getattr(existing, "name", None)
            return await self.player_repository.upsert_one(db, normalized_player_data)

        if resolution.status == "CREATE_NEW":
            if expected_local_player_id is not None:
                raise RuntimeError(
                    f"Expected existing player for provider={provider} provider_id={provider_id}"
                )
            if normalized_player_data.get("name") is None:
                raise ValueError("name is required for new Player Master records")
            return await self.player_repository.upsert_one(db, normalized_player_data)

        raise ValueError(f"Player identity resolution failed: {resolution.status} ({resolution.reason or resolution.evidence or 'unknown reason'})")

    async def sync_team_squad(
        self, db: AsyncSession, team_id: int
    ) -> Dict[str, Any]:
        """
        Sync all players from a team squad.
        
        Fetches /players/squads for team and creates/updates all players.
        """
        try:
            squad_response = await self.player_provider.get_team_squad(team_id)

            if not squad_response or "response" not in squad_response:
                return {"success": False, "error": "No squad data from provider"}

            teams_data = squad_response.get("response", [])
            if not teams_data:
                return {"success": False, "error": "Empty squad response"}

            # Extract players from first team in response
            team_data = teams_data[0] if isinstance(teams_data, list) else {}
            squad_players = team_data.get("players", [])

            if not squad_players:
                return {"success": True, "created": 0, "updated": 0, "message": "No players in squad"}

            # Normalize all players
            normalized_players = []
            for squad_player in squad_players:
                normalized = self.normalize_squad_player(squad_player)
                if normalized and normalized.get("provider_id"):
                    normalized_players.append(normalized)

            # Upsert all
            result = await self.ensure_players_exist(db, normalized_players)
            membership_result = await self.membership_service.record_current_squad(
                db,
                provider_team_id=team_id,
                provider_player_ids=[
                    player["provider_id"]
                    for player in normalized_players
                    if player.get("provider_id")
                ],
            )
            result["membership"] = membership_result
            return result

        except Exception as e:
            logger.error(f"Error syncing team squad for team {team_id}: {e}")
            return {"success": False, "error": str(e)}

    async def sync_fixture_lineups(
        self, db: AsyncSession, fixture_id: int
    ) -> Dict[str, Any]:
        """
        Sync all players from fixture lineups.
        """
        try:
            lineups_response = await self.player_provider.get_fixture_lineups(fixture_id)

            if not lineups_response or "response" not in lineups_response:
                return {"success": False, "error": "No lineups data from provider"}

            lineups = lineups_response.get("response", [])

            all_players = []
            for lineup in lineups:
                if not isinstance(lineup, dict):
                    continue

                for section_key in ("startXI", "substitutes"):
                    players_section = lineup.get(section_key, [])
                    for player_entry in players_section:
                        normalized = self.normalize_lineup_player(player_entry)
                        if normalized and normalized.get("provider_id"):
                            all_players.append(normalized)

            if not all_players:
                return {"success": True, "created": 0, "updated": 0, "message": "No players in lineups"}

            result = await self.ensure_players_exist(db, all_players)
            return result

        except Exception as e:
            logger.error(f"Error syncing lineups for fixture {fixture_id}: {e}")
            return {"success": False, "error": str(e)}

    async def sync_fixture_events(
        self, db: AsyncSession, fixture_id: int
    ) -> Dict[str, Any]:
        """
        Sync all players from fixture events.
        """
        try:
            events_response = await self.player_provider.get_fixture_events(fixture_id)

            if not events_response or "response" not in events_response:
                return {"success": False, "error": "No events data from provider"}

            events = events_response.get("response", [])

            all_players = []
            for event in events:
                if not isinstance(event, dict):
                    continue

                # Extract player
                player_normalized = self.normalize_event_player(event)
                if player_normalized and player_normalized.get("provider_id"):
                    all_players.append(player_normalized)

                # Extract assist player
                assist_normalized = self.normalize_event_assist(event)
                if assist_normalized and assist_normalized.get("provider_id"):
                    all_players.append(assist_normalized)

            if not all_players:
                return {"success": True, "created": 0, "updated": 0, "message": "No players in events"}

            # Deduplicate by provider_id
            seen = set()
            unique_players = []
            for player in all_players:
                pid = player.get("provider_id")
                if pid not in seen:
                    seen.add(pid)
                    unique_players.append(player)

            result = await self.ensure_players_exist(db, unique_players)
            return result

        except Exception as e:
            logger.error(f"Error syncing events for fixture {fixture_id}: {e}")
            return {"success": False, "error": str(e)}
