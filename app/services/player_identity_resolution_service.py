from dataclasses import dataclass
from typing import Any

from sqlalchemy.exc import MultipleResultsFound

from app.repositories.player_repository import PlayerRepository
from app.services.player_sync_service import PlayerSyncService


PLAYER_READINESS_STATES = {
    "READY",
    "MISSING",
    "INVALID",
    "AMBIGUOUS",
    "SYNC_REQUIRED",
    "SYNC_UNAVAILABLE",
    "TEAM_CONFLICT",
    "PERMANENT_IDENTITY_FAILURE",
}


@dataclass(frozen=True)
class PlayerReadiness:
    status: str
    player_id: int | None
    provider: str
    provider_player_id: str | None
    team_id: int | str | None
    player_name: str | None
    failure_reason: str | None = None
    retryable: bool = False
    terminal: bool = False
    lineup_side: str | None = None
    lineup_position: int | None = None
    roster_role: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


class PlayerIdentityResolutionService:
    """Resolve provider player objects to canonical Player Master identities."""

    def __init__(
        self,
        *,
        player_repository: PlayerRepository | None = None,
        player_sync_service: PlayerSyncService | None = None,
        provider: str = "api-football",
    ) -> None:
        self.player_repository = player_repository or PlayerRepository()
        self.player_sync_service = player_sync_service or PlayerSyncService(
            player_repository=self.player_repository,
        )
        self.provider = provider

    def _failure(self, status: str, player_data: Any, team_id: Any, reason: str, *, retryable: bool, terminal: bool) -> PlayerReadiness:
        provider_player_id = None
        player_name = None
        if isinstance(player_data, dict):
            provider_player_id = player_data.get("id")
            player_name = player_data.get("name")
        return PlayerReadiness(
            status=status,
            player_id=None,
            provider=self.provider,
            provider_player_id=str(provider_player_id) if provider_player_id is not None else None,
            team_id=team_id,
            player_name=player_name,
            failure_reason=reason,
            retryable=retryable,
            terminal=terminal,
        )

    async def resolve(
        self,
        db,
        player_data: Any,
        *,
        team_id: int | str | None = None,
        lineup_side: str | None = None,
        lineup_position: int | None = None,
        roster_role: str | None = None,
    ) -> PlayerReadiness:
        if not isinstance(player_data, dict):
            result = self._failure("INVALID", player_data, team_id, "player object is invalid", retryable=False, terminal=True)
            return PlayerReadiness(**{**result.as_dict(), "lineup_side": lineup_side, "lineup_position": lineup_position, "roster_role": roster_role})
        payload_provider = player_data.get("provider")
        if payload_provider is not None and str(payload_provider) != self.provider:
            result = self._failure("INVALID", player_data, team_id, "provider namespace is invalid", retryable=False, terminal=True)
            return PlayerReadiness(**{**result.as_dict(), "lineup_side": lineup_side, "lineup_position": lineup_position, "roster_role": roster_role})
        payload_team_id = player_data.get("team_id")
        if payload_team_id is not None and str(payload_team_id) != str(team_id):
            result = self._failure("TEAM_CONFLICT", player_data, team_id, "provider player team conflicts with lineup team", retryable=False, terminal=True)
            return PlayerReadiness(**{**result.as_dict(), "lineup_side": lineup_side, "lineup_position": lineup_position, "roster_role": roster_role})
        if "id" not in player_data or player_data.get("id") is None:
            result = self._failure("MISSING", player_data, team_id, "provider player id is missing", retryable=False, terminal=True)
            return PlayerReadiness(**{**result.as_dict(), "lineup_side": lineup_side, "lineup_position": lineup_position, "roster_role": roster_role})

        provider_player_id = str(player_data["id"]).strip()
        if not provider_player_id:
            result = self._failure("INVALID", player_data, team_id, "provider player id is empty", retryable=False, terminal=True)
            return PlayerReadiness(**{**result.as_dict(), "lineup_side": lineup_side, "lineup_position": lineup_position, "roster_role": roster_role})

        try:
            player = await self.player_repository.get_by_provider_id(db, provider_player_id, self.provider)
        except MultipleResultsFound:
            result = self._failure("AMBIGUOUS", player_data, team_id, "multiple canonical Players matched provider identity", retryable=False, terminal=True)
            return PlayerReadiness(**{**result.as_dict(), "lineup_side": lineup_side, "lineup_position": lineup_position, "roster_role": roster_role})
        except Exception as exc:
            result = self._failure("SYNC_UNAVAILABLE", player_data, team_id, str(exc), retryable=True, terminal=False)
            return PlayerReadiness(**{**result.as_dict(), "lineup_side": lineup_side, "lineup_position": lineup_position, "roster_role": roster_role})

        if player is None:
            try:
                normalized = self.player_sync_service.normalize_lineup_player({"player": player_data})
                if not normalized:
                    result = self._failure("INVALID", player_data, team_id, "player data could not be normalized", retryable=False, terminal=True)
                    return PlayerReadiness(**{**result.as_dict(), "lineup_side": lineup_side, "lineup_position": lineup_position, "roster_role": roster_role})
                await self.player_sync_service.upsert_player(db, normalized)
                player = await self.player_repository.get_by_provider_id(db, provider_player_id, self.provider)
            except Exception as exc:
                result = self._failure("SYNC_UNAVAILABLE", player_data, team_id, f"Player ensure failed: {exc}", retryable=True, terminal=False)
                return PlayerReadiness(**{**result.as_dict(), "lineup_side": lineup_side, "lineup_position": lineup_position, "roster_role": roster_role})

        if player is None:
            result = self._failure("PERMANENT_IDENTITY_FAILURE", player_data, team_id, "Player remains absent after ensure", retryable=False, terminal=True)
            return PlayerReadiness(**{**result.as_dict(), "lineup_side": lineup_side, "lineup_position": lineup_position, "roster_role": roster_role})

        return PlayerReadiness(
            status="READY",
            player_id=int(player.player_id),
            provider=self.provider,
            provider_player_id=provider_player_id,
            team_id=team_id,
            player_name=player_data.get("name"),
            lineup_side=lineup_side,
            lineup_position=lineup_position,
            roster_role=roster_role,
        )

    async def resolve_lineup(self, db, lineup_payload: list[dict]) -> tuple[list[PlayerReadiness], list[dict]]:
        results: list[PlayerReadiness] = []
        canonical_payload = []
        for lineup in lineup_payload:
            team = lineup.get("team") if isinstance(lineup, dict) else None
            provider_team_id = team.get("id") if isinstance(team, dict) else None
            canonical_lineup = dict(lineup)
            for section, role in (("startXI", "STARTER"), ("substitutes", "SUBSTITUTE")):
                canonical_entries = []
                for position, entry in enumerate(lineup.get(section, []), start=1):
                    player_data = entry.get("player") if isinstance(entry, dict) else None
                    readiness = await self.resolve(
                        db,
                        player_data,
                        team_id=provider_team_id,
                        lineup_side=str(provider_team_id),
                        lineup_position=position,
                        roster_role=role,
                    )
                    results.append(readiness)
                    canonical_entry = dict(entry) if isinstance(entry, dict) else {"player": player_data}
                    canonical_player = dict(player_data) if isinstance(player_data, dict) else {}
                    if readiness.status == "READY":
                        canonical_player["player_id"] = readiness.player_id
                    canonical_entry["player"] = canonical_player
                    canonical_entry["roster_role"] = role
                    canonical_entry["lineup_position"] = position
                    canonical_entries.append(canonical_entry)
                canonical_lineup[section] = canonical_entries
            canonical_payload.append(canonical_lineup)
        return results, canonical_payload