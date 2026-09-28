from dataclasses import dataclass
from typing import Any

from sqlalchemy.exc import MultipleResultsFound

from app.repositories.player_repository import PlayerRepository


@dataclass(frozen=True)
class PlayerIdentityResolutionResult:
    """Explicit system-wide player identity resolution result.

    Status vocabulary mirrors the requested phase contract:
    RESOLVED_EXISTING, CREATE_NEW, AMBIGUOUS, CONFLICT, INVALID.
    """

    status: str
    provider: str
    provider_id: str | None
    local_player_id: int | None = None
    evidence: str | None = None
    reason: str | None = None
    player_name: str | None = None


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
        player_sync_service: object | None = None,
        provider: str = "api-football",
    ) -> None:
        self.player_repository = player_repository or PlayerRepository()
        self.player_sync_service = player_sync_service
        self.provider = provider

    @staticmethod
    def _normalize_provider(provider: str | None) -> str | None:
        value = str(provider or "").strip().casefold()
        return value or None

    @staticmethod
    def _normalize_name(name: Any) -> str:
        return str(name or "").strip().casefold()

    async def resolve_provider_player_identity(
        self,
        db,
        provider: str,
        provider_id: str | int | None,
        *,
        player_data: dict[str, Any] | None = None,
    ) -> PlayerIdentityResolutionResult:
        """Return an explicit, reusable Player Identity Resolver contract.

        Resolution order intentionally follows the requested policy:
        1. validate provider identity
        2. lookup exact (provider, provider_id)
        3. if exact match exists -> RESOLVED_EXISTING
        4. otherwise look across eligible local player candidates
        5. evaluate provider evidence
        6. one clear canonical match -> RESOLVED_EXISTING
        7. multiple possible matches -> AMBIGUOUS
        8. ownership conflict -> CONFLICT
        9. no existing match -> CREATE_NEW
        """
        normalized_provider = self._normalize_provider(provider)
        if not normalized_provider:
            return PlayerIdentityResolutionResult(
                status="INVALID",
                provider="",
                provider_id=str(provider_id) if provider_id is not None else None,
                evidence="provider identity is empty",
                reason="provider namespace is invalid",
            )

        normalized_provider_id = str(provider_id).strip() if provider_id is not None else ""
        if not normalized_provider_id:
            return PlayerIdentityResolutionResult(
                status="INVALID",
                provider=normalized_provider,
                provider_id=None,
                evidence="provider player id is empty",
                reason="provider identity validation failed",
            )

        existing = await self.player_repository.get_by_provider_id(db, normalized_provider_id, normalized_provider)
        if existing is not None:
            try:
                local_player_id = int(existing.player_id)
            except Exception:
                local_player_id = getattr(existing, "player_id", None)
            return PlayerIdentityResolutionResult(
                status="RESOLVED_EXISTING",
                provider=normalized_provider,
                provider_id=normalized_provider_id,
                local_player_id=local_player_id,
                evidence="provider+provider_id exact match",
                reason="existing canonical player found",
                player_name=getattr(existing, "name", None),
            )

        # No exact tuple exists. Inspect eligible local candidates only for careful evidence-backed reuse.
        candidates = []
        if hasattr(self.player_repository, "get_null_provider_candidates"):
            all_players = await self.player_repository.get_null_provider_candidates(db)
        else:
            all_players = await self.player_repository.get_all(db) if hasattr(self.player_repository, "get_all") else []
        for player in all_players:
            current_provider_id = getattr(player, "provider_id", None)
            # Candidate players are players without provider_id; a null-provider row may be reused only
            # when authoritative evidence uniquely supports it. Name alone is not enough.
            if current_provider_id not in (None, ""):
                continue
            name_ok = False
            nationality_ok = True
            if isinstance(player_data, dict):
                incoming_name = self._normalize_name(player_data.get("name"))
                local_name = self._normalize_name(getattr(player, "name", None))
                if incoming_name and local_name and incoming_name == local_name:
                    name_ok = True
                incoming_nationality = str(player_data.get("nationality") or "").strip().casefold()
                local_nationality = str(getattr(player, "nationality", None) or "").strip().casefold()
                if incoming_nationality and local_nationality and incoming_nationality != local_nationality:
                    nationality_ok = False
            if name_ok and nationality_ok:
                candidates.append(player)

        if len(candidates) == 1:
            player = candidates[0]
            return PlayerIdentityResolutionResult(
                status="RESOLVED_EXISTING",
                provider=normalized_provider,
                provider_id=normalized_provider_id,
                local_player_id=int(player.player_id),
                evidence="authoritative local name/nationality evidence matched a single empty-provider candidate",
                reason="one clear canonical local candidate found",
                player_name=getattr(player, "name", None),
            )

        if len(candidates) > 1:
            return PlayerIdentityResolutionResult(
                status="AMBIGUOUS",
                provider=normalized_provider,
                provider_id=normalized_provider_id,
                evidence="multiple null-provider local candidates pass evidence review",
                reason="name-only or insufficient evidence cannot safely select canonical local player",
            )

        # No eligible player owner shows evidence; if the dialect of the repo can separately express
        # ownership conflict by a current same provider+provider_id pair, the exact lookup above is the
        # authoritative route. That protects the reads and avoids silent historical migration.
        return PlayerIdentityResolutionResult(
            status="CREATE_NEW",
            provider=normalized_provider,
            provider_id=normalized_provider_id,
            evidence="no local player owner found for provider+provider_id",
            reason="safe create_new path without historical reconciliation",
        )

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
            result = self._failure("MISSING", player_data, team_id, "Player Master identity is unavailable", retryable=False, terminal=True)
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
                    provider_player_id = None
                    if isinstance(player_data, dict):
                        provider_player_id = player_data.get("id") or player_data.get("provider_id")

                    resolution = await self.resolve_provider_player_identity(
                        db,
                        self.provider,
                        provider_player_id,
                        player_data=player_data,
                    ) if provider_player_id is not None else None

                    if resolution is None:
                        readiness = self._failure("MISSING", player_data, provider_team_id, "provider player id is missing", retryable=True, terminal=False)
                    elif resolution.status in {"RESOLVED_EXISTING", "CREATE_NEW"}:
                        local_player_id = resolution.local_player_id
                        if local_player_id is None and resolution.status == "CREATE_NEW":
                            if isinstance(player_data, dict):
                                normalized = {
                                    "provider": self.provider,
                                    "provider_id": str(provider_player_id),
                                    "name": str(player_data.get("name") or "").strip(),
                                    "nationality": player_data.get("nationality"),
                                    "position": player_data.get("position") or player_data.get("pos"),
                                    "photo": player_data.get("photo"),
                                    "height": player_data.get("height"),
                                    "weight": player_data.get("weight"),
                                    "preferred_foot": player_data.get("preferred_foot"),
                                    "birth_date": player_data.get("birth_date"),
                                    "birth_place": player_data.get("birth_place"),
                                    "birth_country": player_data.get("birth_country"),
                                    "first_name": player_data.get("first_name"),
                                    "last_name": player_data.get("last_name"),
                                }
                                player_sync_service = self.player_sync_service
                                if player_sync_service is None:
                                    from app.services.player_sync_service import PlayerSyncService

                                    player_sync_service = PlayerSyncService(
                                        player_repository=self.player_repository,
                                        player_identity_resolution_service=self,
                                    )
                                created = await player_sync_service.upsert_player(db, normalized)
                                local_player_id = created.player_id
                            else:
                                local_player_id = None
                        if local_player_id is None:
                            readiness = self._failure("INVALID", player_data, provider_team_id, "player identity could not be resolved", retryable=False, terminal=True)
                        else:
                            readiness = PlayerReadiness(
                                status="READY",
                                player_id=int(local_player_id),
                                provider=self.provider,
                                provider_player_id=str(provider_player_id) if provider_player_id is not None else None,
                                team_id=provider_team_id,
                                player_name=player_data.get("name") if isinstance(player_data, dict) else None,
                                lineup_side=str(provider_team_id),
                                lineup_position=position,
                                roster_role=role,
                            )
                    else:
                        mapped_status = {
                            "AMBIGUOUS": "AMBIGUOUS",
                            "CONFLICT": "TEAM_CONFLICT",
                            "INVALID": "INVALID",
                        }.get(resolution.status, "INVALID")
                        readiness = self._failure(
                            mapped_status,
                            player_data,
                            provider_team_id,
                            resolution.reason or resolution.evidence or "player identity resolution failed",
                            retryable=False,
                            terminal=True,
                        )
                        readiness = PlayerReadiness(**{**readiness.as_dict(), "lineup_side": str(provider_team_id), "lineup_position": position, "roster_role": role})

                    results.append(readiness)
                    canonical_entry = dict(entry) if isinstance(entry, dict) else {"player": player_data}
                    canonical_player = dict(player_data) if isinstance(player_data, dict) else {}
                    if readiness.status == "READY":
                        canonical_player["player_id"] = readiness.player_id
                        canonical_player["local_player_id"] = readiness.player_id
                    else:
                        canonical_player.pop("player_id", None)
                        canonical_player.pop("local_player_id", None)
                    canonical_entry["player"] = canonical_player
                    canonical_entry["roster_role"] = role
                    canonical_entry["lineup_position"] = position
                    canonical_entries.append(canonical_entry)
                canonical_lineup[section] = canonical_entries
            canonical_payload.append(canonical_lineup)
        return results, canonical_payload