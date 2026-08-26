import logging
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.player import Player
from app.providers.player_provider import PlayerProvider
from app.repositories.player_repository import PlayerRepository
from app.services.player_team_membership_service import PlayerTeamMembershipService

logger = logging.getLogger(__name__)


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
    ) -> None:
        from app.services.base.football_client import FootballAPIClient

        self.client = FootballAPIClient()
        self.player_provider = player_provider or PlayerProvider(self.client)
        self.player_repository = player_repository or PlayerRepository()
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
                    db, provider_id, "api-football"
                )
                await self.player_repository.upsert_one(db, player_data)
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

    async def upsert_player(self, db: AsyncSession, player_data: dict) -> Player:
        """
        Upsert a single player by (provider, provider_id).
        
        Safe for idempotent operations.
        """
        return await self.player_repository.upsert_one(db, player_data)

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
