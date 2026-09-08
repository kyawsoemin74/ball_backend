import asyncio
from types import SimpleNamespace

from sqlalchemy.exc import MultipleResultsFound

from app.services.player_identity_resolution_service import PlayerIdentityResolutionService
from app.services.lineup_sync_service import LineupSyncService


class PlayerRepository:
    def __init__(self, player=None, error=None):
        self.player = player
        self.error = error
        self.calls = []

    async def get_by_provider_id(self, db, provider_id, provider="api-football"):
        self.calls.append((db, provider_id, provider))
        if self.error:
            raise self.error
        return self.player


class PlayerSync:
    def __init__(self, player):
        self.player = player
        self.sessions = []

    def normalize_lineup_player(self, entry):
        return {"provider": "api-football", "provider_id": str(entry["player"]["id"]), "name": entry["player"].get("name", "")}

    async def upsert_player(self, db, data):
        self.sessions.append((db, data))
        self.repository.player = self.player
        return self.player


def resolve(player=None, sync_player=None, **kwargs):
    repository = PlayerRepository(player=player, error=kwargs.pop("error", None))
    sync = PlayerSync(sync_player) if sync_player is not None else PlayerSync(player)
    service = PlayerIdentityResolutionService(player_repository=repository, player_sync_service=sync)
    result = asyncio.run(service.resolve(object(), kwargs.pop("payload", {"id": 9, "name": "Player"}), team_id=1))
    return result, repository, sync


def test_ready_existing_player():
    result, repository, sync = resolve(player=SimpleNamespace(player_id=44))
    assert result.status == "READY"
    assert result.player_id == 44
    assert not sync.sessions
    assert len(repository.calls) == 1


def test_ready_after_player_ensure_uses_caller_session():
    session = object()
    repository = PlayerRepository(player=None)
    player = SimpleNamespace(player_id=44)
    sync = PlayerSync(player)
    sync.repository = repository
    service = PlayerIdentityResolutionService(player_repository=repository, player_sync_service=sync)
    result = asyncio.run(service.resolve(session, {"id": 9, "name": "Player"}, team_id=1))
    assert result.status == "READY"
    assert sync.sessions[0][0] is session
    assert repository.calls[0][0] is session


def test_missing_id_is_terminal_and_never_synced():
    result, repository, sync = resolve(payload={"name": "Player"})
    assert result.status == "MISSING"
    assert result.terminal is True
    assert result.retryable is False
    assert not sync.sessions
    assert not repository.calls


def test_invalid_payload_is_terminal():
    result, _, _ = resolve(payload=None)
    assert result.status == "INVALID"
    assert result.terminal is True


def test_sync_unavailable_is_retryable():
    result, _, _ = resolve(error=RuntimeError("database unavailable"))
    assert result.status == "SYNC_UNAVAILABLE"
    assert result.retryable is True
    assert result.terminal is False


def test_ambiguous_identity_is_terminal():
    result, _, _ = resolve(error=MultipleResultsFound())
    assert result.status == "AMBIGUOUS"
    assert result.terminal is True


def test_one_unresolved_player_blocks_lineup_before_repository_write():
    class Provider:
        async def get_match_lineup(self, match_id):
            return {"response": [{"team": {"id": 1}, "startXI": [{"player": {"id": 9, "name": "Missing"}}], "substitutes": []}]}

    class Resolver:
        async def resolve_lineup(self, db, payload):
            from app.services.player_identity_resolution_service import PlayerReadiness
            return [PlayerReadiness("MISSING", None, "api-football", None, 1, "Missing", "missing", False, True)], payload

    class Repository:
        def __init__(self):
            self.writes = 0

        async def get_match_status(self, db, match_id):
            return SimpleNamespace(status="NS")

        async def get_by_match_id(self, db, match_id):
            return None

        async def create_one(self, db, match_id, data):
            self.writes += 1

    class Projection:
        def __init__(self):
            self.calls = 0

        async def project_lineup(self, db, match_id, payload):
            self.calls += 1
            return {"success": True}

    repository = Repository()
    projection = Projection()
    service = LineupSyncService(
        lineup_provider=Provider(),
        lineup_repository=repository,
        analytics_projection_service=projection,
        player_identity_resolution_service=Resolver(),
    )
    result = asyncio.run(service.sync_lineup(object(), 123, validate_lineup=lambda value: True))
    assert result["success"] is False
    assert result["failure_classification"] == "MISSING"
    assert result["diagnostics"][0]["provider_player_id"] is None
    assert result["diagnostics"][0]["fixture_id"] == 123
    assert repository.writes == 0
    assert projection.calls == 0
