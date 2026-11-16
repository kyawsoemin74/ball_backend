import asyncio
from types import SimpleNamespace

from sqlalchemy.exc import MultipleResultsFound

from app.services.player_identity_resolution_service import PlayerIdentityResolutionService
from app.services.lineup_sync_service import LineupSyncService
from app.repositories.missing_lineup_identity_repository import MissingLineupIdentityRepository


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


def test_resolve_provider_player_identity_requires_exact_provider_tuple_first():
    class Repo:
        async def get_by_provider_id(self, db, provider_id, provider="api-football"):
            if provider == "api-football" and provider_id == "100":
                return SimpleNamespace(player_id=101, provider="api-football", provider_id="100", name="Known Player")
            return None

        async def get_all(self, db):
            return []

    service = PlayerIdentityResolutionService(player_repository=Repo())
    result = asyncio.run(service.resolve_provider_player_identity(None, "api-football", "100", player_data={"id": "100", "name": "Known Player"}))

    assert result.status == "RESOLVED_EXISTING"
    assert result.local_player_id == 101
    assert result.provider == "api-football"
    assert result.provider_id == "100"
    assert result.evidence == "provider+provider_id exact match"


def test_ready_existing_player():
    result, repository, sync = resolve(player=SimpleNamespace(player_id=44))
    assert result.status == "READY"
    assert result.player_id == 44
    assert not sync.sessions
    assert len(repository.calls) == 1


def test_missing_player_is_not_created_by_lineup_resolution():
    session = object()
    repository = PlayerRepository(player=None)
    player = SimpleNamespace(player_id=44)
    sync = PlayerSync(player)
    sync.repository = repository
    service = PlayerIdentityResolutionService(player_repository=repository, player_sync_service=sync)
    result = asyncio.run(service.resolve(session, {"id": 9, "name": "Player"}, team_id=1))
    assert result.status == "MISSING"
    assert result.terminal is True
    assert not sync.sessions
    assert repository.calls[0][0] is session


def test_missing_id_is_retryable_and_never_synced():
    result, repository, sync = resolve(payload={"name": "Player"})
    assert result.status == "MISSING"
    assert result.terminal is False
    assert result.retryable is True
    assert not sync.sessions
    assert not repository.calls


def test_missing_canonical_player_with_provider_id_remains_terminal():
    result, repository, sync = resolve(payload={"id": 42, "name": "Player"})

    assert result.status == "MISSING"
    assert result.terminal is True
    assert result.retryable is False
    assert repository.calls[0][1:] == ("42", "api-football")
    assert not sync.sessions


def test_provider_id_fallback_is_used_by_resolve():
    result, repository, sync = resolve(payload={"id": " ", "provider_id": "42", "name": "Player"})

    assert result.status == "MISSING"
    assert repository.calls[0][1:] == ("42", "api-football")
    assert not sync.sessions


def test_missing_player_master_is_terminal_and_never_synced():
    result, repository, sync = resolve(payload={"id": 99, "name": "Unknown"})
    assert result.status == "MISSING"
    assert result.terminal is True
    assert not sync.sessions
    assert len(repository.calls) == 1


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


def test_lineup_resolution_upserts_photo_for_exact_existing_identity():
    from app.services.player_sync_service import PlayerSyncService

    class Repository:
        def __init__(self, player=None):
            self.player = player
            self.upsert_calls = []

        async def get_by_provider_id(self, db, provider_id, provider="api-football"):
            if (
                self.player is not None
                and self.player.provider == provider
                and self.player.provider_id == str(provider_id)
            ):
                return self.player
            return None

        async def get_null_provider_candidates(self, db):
            return []

        async def upsert_one(self, db, player_data):
            self.upsert_calls.append(dict(player_data))
            if self.player is None:
                self.player = SimpleNamespace(player_id=77, **player_data)
            else:
                for key, value in player_data.items():
                    if key not in {"provider", "provider_id"} and value is not None:
                        setattr(self.player, key, value)
            return self.player

    existing = SimpleNamespace(
        player_id=77,
        provider="api-football",
        provider_id="9",
        name="Existing Player",
        photo=None,
        position="Defender",
    )
    repository = Repository(existing)
    service = PlayerIdentityResolutionService(player_repository=repository)
    service.player_sync_service = PlayerSyncService(
        player_repository=repository,
        player_identity_resolution_service=service,
    )
    lineups = [{
        "team": {"id": 1},
        "startXI": [{
            "player": {
                "id": 9,
                "name": "Updated Player",
                "photo": "https://img/player.png",
                "pos": "F",
            },
        }],
        "substitutes": [],
    }]

    readiness, canonical = asyncio.run(service.resolve_lineup(object(), lineups))

    assert readiness[0].status == "READY"
    assert readiness[0].player_id == 77
    assert repository.player.player_id == 77
    assert repository.player.photo == "https://img/player.png"
    assert repository.player.position == "Defender"
    assert len(repository.upsert_calls) == 1
    assert repository.upsert_calls[0]["position"] is None
    assert canonical[0]["startXI"][0]["player"]["local_player_id"] == 77
    assert canonical[0]["startXI"][0]["player"]["photo"] == "https://img/player.png"


def test_lineup_creation_is_idempotent_and_preserves_canonical_player_id():
    from app.services.player_sync_service import PlayerSyncService

    class Repository:
        def __init__(self):
            self.player = None
            self.upsert_calls = []

        async def get_by_provider_id(self, db, provider_id, provider="api-football"):
            if (
                self.player is not None
                and self.player.provider == provider
                and self.player.provider_id == str(provider_id)
            ):
                return self.player
            return None

        async def get_null_provider_candidates(self, db):
            return []

        async def upsert_one(self, db, player_data):
            self.upsert_calls.append(dict(player_data))
            if self.player is None:
                self.player = SimpleNamespace(player_id=77, **player_data)
            else:
                for key, value in player_data.items():
                    if key not in {"provider", "provider_id"} and value is not None:
                        setattr(self.player, key, value)
            return self.player

    repository = Repository()
    service = PlayerIdentityResolutionService(player_repository=repository)
    service.player_sync_service = PlayerSyncService(
        player_repository=repository,
        player_identity_resolution_service=service,
    )
    lineups = [{
        "team": {"id": 1},
        "startXI": [{
            "player": {
                "id": 9,
                "name": "New Player",
                "photo": "https://img/player.png",
            },
        }],
        "substitutes": [],
    }]

    first_readiness, first_canonical = asyncio.run(service.resolve_lineup(object(), lineups))
    second_readiness, second_canonical = asyncio.run(service.resolve_lineup(object(), lineups))

    assert first_readiness[0].player_id == second_readiness[0].player_id == 77
    assert first_canonical[0]["startXI"][0]["player"]["local_player_id"] == 77
    assert second_canonical[0]["startXI"][0]["player"]["local_player_id"] == 77
    assert len(repository.upsert_calls) == 2
    assert repository.player.photo == "https://img/player.png"


def test_legacy_null_provider_candidate_is_not_upserted_by_lineup_resolution():
    class Repository:
        def __init__(self):
            self.player = SimpleNamespace(
                player_id=77,
                provider="api-football",
                provider_id=None,
                name="Legacy Player",
                nationality="Brazil",
            )
            self.upsert_calls = []

        async def get_by_provider_id(self, db, provider_id, provider="api-football"):
            return None

        async def get_null_provider_candidates(self, db):
            return [self.player]

        async def upsert_one(self, db, player_data):
            self.upsert_calls.append(player_data)
            return self.player

    repository = Repository()
    service = PlayerIdentityResolutionService(player_repository=repository)
    service.player_sync_service = SimpleNamespace(
        upsert_player=lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("legacy identities must not be upserted")
        )
    )

    readiness, canonical = asyncio.run(service.resolve_lineup(object(), [{
        "team": {"id": 1},
        "startXI": [{
            "player": {
                "id": 9,
                "name": "Legacy Player",
                "nationality": "Brazil",
                "photo": "https://img/player.png",
            },
        }],
        "substitutes": [],
    }]))

    assert readiness[0].status == "READY"
    assert readiness[0].player_id == 77
    assert repository.upsert_calls == []
    assert canonical[0]["startXI"][0]["player"]["local_player_id"] == 77


def test_lineup_upsert_rejects_mismatch_between_exact_identity_and_local_id():
    class Repository:
        def __init__(self):
            self.player = SimpleNamespace(
                player_id=77,
                provider="api-football",
                provider_id="9",
                name="Player",
            )
            self.upsert_calls = []

        async def get_by_provider_id(self, db, provider_id, provider="api-football"):
            return self.player

        async def upsert_one(self, db, player_data):
            self.upsert_calls.append(player_data)

    class Resolver(PlayerIdentityResolutionService):
        async def resolve_provider_player_identity(
            self, db, provider, provider_id, *, player_data=None
        ):
            return SimpleNamespace(
                status="RESOLVED_EXISTING",
                provider=provider,
                provider_id=str(provider_id),
                local_player_id=99,
                evidence="provider+provider_id exact match",
            )

    repository = Repository()
    service = Resolver(player_repository=repository)
    service.player_sync_service = SimpleNamespace(
        upsert_player=lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("mismatched identities must not be upserted")
        )
    )

    readiness, _ = asyncio.run(service.resolve_lineup(object(), [{
        "team": {"id": 1},
        "startXI": [{"player": {"id": 9, "name": "Player"}}],
        "substitutes": [],
    }]))

    assert readiness[0].status == "INVALID"
    assert repository.upsert_calls == []


def test_ambiguous_lineup_identity_is_not_upserted():
    class Repository:
        def __init__(self):
            self.candidates = [
                SimpleNamespace(player_id=77, provider_id=None, name="Player", nationality="Brazil"),
                SimpleNamespace(player_id=78, provider_id=None, name="Player", nationality="Brazil"),
            ]
            self.upsert_calls = []

        async def get_by_provider_id(self, db, provider_id, provider="api-football"):
            return None

        async def get_null_provider_candidates(self, db):
            return self.candidates

        async def upsert_one(self, db, player_data):
            self.upsert_calls.append(player_data)

    repository = Repository()
    service = PlayerIdentityResolutionService(player_repository=repository)
    service.player_sync_service = SimpleNamespace(
        upsert_player=lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("ambiguous identities must not be upserted")
        )
    )

    readiness, _ = asyncio.run(service.resolve_lineup(object(), [{
        "team": {"id": 1},
        "startXI": [{
            "player": {
                "id": 9,
                "name": "Player",
                "nationality": "Brazil",
                "photo": "https://img/player.png",
            },
        }],
        "substitutes": [],
    }]))

    assert readiness[0].status == "AMBIGUOUS"
    assert repository.upsert_calls == []


def test_all_missing_lineup_is_diagnostics_only_partial_success():
    class Provider:
        async def get_match_lineup(self, match_id):
            return {"response": [
                {"team": {"id": 1}, "startXI": [{"player": {"name": "Missing"}}], "substitutes": []},
                {"team": {"id": 2}, "startXI": [], "substitutes": []},
            ]}

    class Resolver:
        async def resolve_lineup(self, db, payload):
            from app.services.player_identity_resolution_service import PlayerReadiness
            return [PlayerReadiness("MISSING", None, "api-football", None, 1, "Missing", "provider player id is missing", True, False, lineup_side="1", lineup_position=1, roster_role="STARTER")], payload

    class Repository:
        def __init__(self):
            self.writes = 0

        async def get_match_status(self, db, match_id):
            return SimpleNamespace(status="NS", provider="api-football", provider_fixture_id=456, home_team_id=1, away_team_id=2)

        async def get_by_match_id(self, db, match_id):
            return None

        async def create_one(self, db, match_id, data):
            self.writes += 1

    class TeamRepository:
        async def find_by_provider_identity(self, db, provider, provider_id):
            return SimpleNamespace(team_id=int(provider_id))

    repository = Repository()
    service = LineupSyncService(
        lineup_provider=Provider(),
        lineup_repository=repository,
        player_identity_resolution_service=Resolver(),
        team_repository=TeamRepository(),
    )
    result = asyncio.run(service.sync_lineup(object(), 123, validate_lineup=lambda value: True))

    assert result["success"] is True
    assert result["partial"] is True
    assert result["failure_classification"] == "PARTIAL"
    assert result["diagnostics"][0]["provider_player_id"] is None
    assert result["diagnostics"][0]["fixture_id"] == 123
    assert repository.writes == 0


def test_partial_lineup_preserves_complete_data_and_records_diagnostic_metadata():
    from app.services.player_identity_resolution_service import PlayerReadiness

    class Provider:
        async def get_match_lineup(self, match_id):
            return {"response": [
                {
                    "team": {"id": 1},
                    "startXI": [
                        {"player": {"id": 9, "name": "Resolved"}},
                        {"player": {"name": "Missing", "number": 17, "pos": "F", "grid": "1:2"}},
                    ],
                    "substitutes": [],
                },
                {"team": {"id": 2}, "startXI": [], "substitutes": []},
            ]}

    class DB:
        async def execute(self, *_args, **_kwargs):
            return None

        async def flush(self):
            pass

    class Resolver:
        async def resolve_lineup(self, db, payload):
            readiness = [
                PlayerReadiness("READY", 101, "api-football", "9", 1, "Resolved", lineup_side="1", lineup_position=1, roster_role="STARTER"),
                PlayerReadiness("MISSING", None, "api-football", None, 1, "Missing", "provider player id is missing", True, False, lineup_side="1", lineup_position=2, roster_role="STARTER"),
            ]
            canonical = [dict(lineup) for lineup in payload]
            canonical[0]["startXI"] = [
                {
                    "player": {
                        "id": 9,
                        "name": "Resolved",
                        "player_id": 101,
                        "local_player_id": 101,
                    },
                    "roster_role": "STARTER",
                    "lineup_position": 1,
                },
                {
                    "player": {
                        "name": "Missing",
                        "number": 17,
                        "pos": "F",
                        "grid": "1:2",
                    },
                    "roster_role": "STARTER",
                    "lineup_position": 2,
                },
            ]
            return readiness, canonical

    class TeamRepository:
        async def find_by_provider_identity(self, db, provider, provider_id):
            return SimpleNamespace(team_id=int(provider_id))

    class LineupRepository:
        def __init__(self):
            self.existing_data = [
                {"team": {"id": 1}, "startXI": [{"player": {"local_player_id": 501}}], "substitutes": []},
                {"team": {"id": 2}, "startXI": [{"player": {"local_player_id": 502}}], "substitutes": []},
            ]
            self.record = SimpleNamespace(data=self.existing_data)
            self.get_calls = 0
            self.created = []
            self.updated = []

        async def get_match_status(self, db, match_id):
            return SimpleNamespace(status="NS", provider="api-football", provider_fixture_id=456, home_team_id=1, away_team_id=2)

        async def get_by_match_id(self, db, match_id):
            self.get_calls += 1
            return self.record

        async def create_one(self, db, match_id, data):
            self.created.append((match_id, data))

        async def update_one(self, db, record, data):
            self.updated.append(data)
            record.data = data

    class MissingRepository:
        def __init__(self):
            self.missing = []
            self.resolved = []

        async def upsert_missing(self, db, **kwargs):
            self.missing.append(kwargs)

        async def resolve_for_match(self, db, match_id, players):
            self.resolved.append((match_id, players))

    class AnalyticsProjection:
        def __init__(self):
            self.rows = [{"player_id": 501}, {"player_id": 502}]
            self.calls = []

        async def project_lineup(self, db, match_id, payload):
            self.calls.append(payload)
            self.rows = payload
            return {"success": True}

    lineup_repository = LineupRepository()
    missing_repository = MissingRepository()
    analytics = AnalyticsProjection()
    service = LineupSyncService(
        lineup_provider=Provider(),
        lineup_repository=lineup_repository,
        analytics_projection_service=analytics,
        player_identity_resolution_service=Resolver(),
        team_repository=TeamRepository(),
    )
    service.missing_identity_repository = missing_repository

    db = DB()
    result = None
    for _ in range(2):
        result = asyncio.run(service.sync_lineup(db, 123, validate_lineup=lambda value: True))

    assert result["success"] is True
    assert result["partial"] is True
    assert result["created"] is False
    assert result["updated"] is False
    assert lineup_repository.get_calls == 0
    assert lineup_repository.created == []
    assert lineup_repository.updated == []
    assert lineup_repository.record.data is lineup_repository.existing_data
    assert analytics.calls == []
    assert analytics.rows == [{"player_id": 501}, {"player_id": 502}]
    assert len(missing_repository.missing) == 2
    assert result["diagnostics"][0]["provider_player_id"] is None
    assert missing_repository.missing[0]["player_name"] == "Missing"
    assert missing_repository.missing[0]["local_team_id"] == 1
    assert missing_repository.missing[0]["shirt_number"] == 17
    assert missing_repository.missing[0]["position"] == "F"
    assert missing_repository.missing[0]["grid"] == "1:2"
    assert missing_repository.resolved[0][1][0]["player_id"] == 101


def test_multiple_missing_players_remain_partial_without_analytics():
    class Provider:
        async def get_match_lineup(self, match_id):
            return {"response": [
                {
                    "team": {"id": 1},
                    "startXI": [{"player": {"name": "Missing A"}}, {"player": {"name": "Missing B"}}],
                    "substitutes": [],
                },
                {"team": {"id": 2}, "startXI": [], "substitutes": []},
            ]}

    class Resolver:
        async def resolve_lineup(self, db, payload):
            from app.services.player_identity_resolution_service import PlayerReadiness
            return [
                PlayerReadiness("MISSING", None, "api-football", None, 1, "Missing A", "missing", True, False, lineup_side="1", lineup_position=1, roster_role="STARTER"),
                PlayerReadiness("MISSING", None, "api-football", None, 1, "Missing B", "missing", True, False, lineup_side="1", lineup_position=2, roster_role="STARTER"),
            ], payload

    class Repository:
        async def get_match_status(self, db, match_id):
            return SimpleNamespace(status="NS", provider="api-football", provider_fixture_id=456, home_team_id=1, away_team_id=2)
        async def get_by_match_id(self, db, match_id):
            return None

    class TeamRepository:
        async def find_by_provider_identity(self, db, provider, provider_id):
            return SimpleNamespace(team_id=int(provider_id))

    class Projection:
        async def project_lineup(self, db, match_id, payload):
            raise AssertionError("partial lineups must not reach Analytics")

    service = LineupSyncService(
        lineup_provider=Provider(),
        lineup_repository=Repository(),
        analytics_projection_service=Projection(),
        player_identity_resolution_service=Resolver(),
        team_repository=TeamRepository(),
    )
    result = asyncio.run(service.sync_lineup(object(), 123, validate_lineup=lambda value: True))

    assert result["success"] is True
    assert result["partial"] is True
    assert len(result["diagnostics"]) == 2


def test_all_missing_structurally_valid_lineup_is_partial():
    class Provider:
        async def get_match_lineup(self, match_id):
            return {"response": [
                {"team": {"id": 1}, "startXI": [{"player": {"name": "Missing"}}], "substitutes": []},
                {"team": {"id": 2}, "startXI": [], "substitutes": []},
            ]}

    class Resolver:
        async def resolve_lineup(self, db, payload):
            from app.services.player_identity_resolution_service import PlayerReadiness
            return [PlayerReadiness("MISSING", None, "api-football", None, 1, "Missing", "missing", True, False, lineup_side="1", lineup_position=1, roster_role="STARTER")], payload

    class DB:
        async def execute(self, *_args, **_kwargs):
            return None

        async def flush(self):
            pass

    class Repository:
        def __init__(self):
            self.writes = 0

        async def get_match_status(self, db, match_id):
            return SimpleNamespace(status="NS", provider="api-football", provider_fixture_id=456, home_team_id=1, away_team_id=2)
        async def get_by_match_id(self, db, match_id):
            return None
        async def create_one(self, db, match_id, data):
            self.writes += 1

    class TeamRepository:
        async def find_by_provider_identity(self, db, provider, provider_id):
            return SimpleNamespace(team_id=int(provider_id))

    class MissingRepository:
        def __init__(self):
            self.missing = []

        async def upsert_missing(self, db, **kwargs):
            self.missing.append(kwargs)

        async def resolve_for_match(self, db, match_id, players):
            return 0

    class Projection:
        def __init__(self):
            self.calls = []

        async def project_lineup(self, db, match_id, payload):
            self.calls.append(payload)
            return {"success": True}

    lineup_repository = Repository()
    missing_repository = MissingRepository()
    projection = Projection()
    service = LineupSyncService(
        lineup_provider=Provider(),
        lineup_repository=lineup_repository,
        analytics_projection_service=projection,
        player_identity_resolution_service=Resolver(),
        team_repository=TeamRepository(),
    )
    service.missing_identity_repository = missing_repository
    result = asyncio.run(service.sync_lineup(DB(), 123, validate_lineup=lambda value: True))

    assert result["success"] is True
    assert result["partial"] is True
    assert result["failure_classification"] == "PARTIAL"
    assert len(missing_repository.missing) == 1
    assert lineup_repository.writes == 0
    assert projection.calls == []


def test_missing_diagnostic_resolves_and_can_become_missing_again():
    record = SimpleNamespace(
        provider_team_id="1107",
        roster_role="STARTER",
        lineup_position=3,
        player_name="Player",
        resolution_status="MISSING",
        resolved_local_player_id=None,
        retry_eligible=1,
        last_seen=None,
        raw_identity_state="missing",
        observation_count=1,
    )

    class Result:
        def scalar_one_or_none(self):
            return record

        def scalars(self):
            return self

        def all(self):
            return [record]

    class DB:
        flush_calls = 0

        async def execute(self, *_args, **_kwargs):
            return Result()

        async def flush(self):
            self.flush_calls += 1

    db = DB()
    repository = MissingLineupIdentityRepository()
    resolved = asyncio.run(repository.resolve_for_match(db, 123, [{
        "player_id": 77,
        "team_id": "1107",
        "roster_role": "STARTER",
        "lineup_position": 3,
        "player_name": "Resolved Player",
    }]))

    assert resolved == 1
    assert record.resolution_status == "RESOLVED"
    assert record.resolved_local_player_id == 77
    assert record.retry_eligible == 0
    assert record.player_name == "Resolved Player"

    asyncio.run(repository.upsert_missing(
        db,
        match_id=123,
        provider_fixture_id="456",
        local_team_id=85,
        provider_team_id="1107",
        provider="api-football",
        player_name="Player",
        shirt_number=3,
        position="F",
        grid=None,
        roster_role="STARTER",
        lineup_position=3,
        missing_reason="provider player id is missing",
        raw_identity_state="missing",
    ))

    assert record.resolution_status == "MISSING"
    assert record.resolved_local_player_id is None
    assert record.retry_eligible == 1
    assert record.observation_count == 2
