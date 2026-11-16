import asyncio
from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.models.match_lineup import MatchLineup
from app.services.lineup_service import LineupService, make_lineup_cache_key
from app.services.player_sync_service import PlayerSyncService


class IdentityResolver:
    def __init__(self, status, local_player_id=None):
        self.status = status
        self.local_player_id = local_player_id
        self.calls = []

    async def resolve_provider_player_identity(self, db, provider, provider_id, *, player_data=None):
        self.calls.append((provider, str(provider_id), player_data))
        return SimpleNamespace(
            status=self.status,
            local_player_id=self.local_player_id,
            provider=provider,
            provider_id=str(provider_id),
        )


class PlayerRepository:
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

    async def upsert_one(self, db, player_data):
        self.upsert_calls.append(dict(player_data))
        if self.player is None:
            self.player = SimpleNamespace(player_id=77, **player_data)
        else:
            for key, value in player_data.items():
                if key not in {"provider", "provider_id"} and value is not None:
                    setattr(self.player, key, value)
        return self.player


def _player_sync(player, resolver):
    return PlayerSyncService(
        player_repository=player,
        player_identity_resolution_service=resolver,
    )


def test_existing_player_receives_photo_without_creating_duplicate():
    existing = SimpleNamespace(
        player_id=77,
        provider="api-football",
        provider_id="101",
        photo=None,
        name="Existing Player",
    )
    repository = PlayerRepository(existing)
    service = _player_sync(repository, IdentityResolver("RESOLVED_EXISTING", 77))

    result = asyncio.run(service.upsert_player(None, {
        "provider": "api-football",
        "provider_id": "101",
        "photo": "https://img/player.png",
    }))

    assert result is existing
    assert result.player_id == 77
    assert result.photo == "https://img/player.png"
    assert len(repository.upsert_calls) == 1


@pytest.mark.parametrize(
    ("existing_photo", "incoming_photo", "expected_photo"),
    [
        ("https://img/old.png", None, "https://img/old.png"),
        ("https://img/old.png", "https://img/new.png", "https://img/new.png"),
        ("https://img/old.png", "  ", "https://img/old.png"),
    ],
)
def test_existing_photo_uses_null_preserving_upsert(
    existing_photo, incoming_photo, expected_photo
):
    existing = SimpleNamespace(
        player_id=77,
        provider="api-football",
        provider_id="101",
        photo=existing_photo,
    )
    repository = PlayerRepository(existing)
    service = _player_sync(repository, IdentityResolver("RESOLVED_EXISTING", 77))

    asyncio.run(service.upsert_player(None, {
        "provider": "api-football",
        "provider_id": "101",
        "photo": incoming_photo,
        "name": None,
    }))

    assert existing.photo == expected_photo
    assert repository.upsert_calls[0]["photo"] == (
        incoming_photo.strip() or None if incoming_photo else None
    )


def test_existing_non_null_fields_survive_incoming_null_values():
    existing = SimpleNamespace(
        player_id=77,
        provider="api-football",
        provider_id="101",
        photo="https://img/old.png",
        name="Existing Player",
        nationality="Brazil",
    )
    repository = PlayerRepository(existing)
    service = _player_sync(repository, IdentityResolver("RESOLVED_EXISTING", 77))

    asyncio.run(service.upsert_player(None, {
        "provider": "api-football",
        "provider_id": "101",
        "photo": None,
        "name": None,
        "nationality": None,
    }))

    assert existing.photo == "https://img/old.png"
    assert existing.name == "Existing Player"
    assert existing.nationality == "Brazil"


def test_new_player_is_created_with_photo():
    repository = PlayerRepository()
    service = _player_sync(repository, IdentityResolver("CREATE_NEW"))

    result = asyncio.run(service.upsert_player(None, {
        "provider": "api-football",
        "provider_id": "101",
        "photo": "https://img/player.png",
    }))

    assert result.photo == "https://img/player.png"
    assert len(repository.upsert_calls) == 1


@pytest.mark.parametrize(
    ("existing", "resolved_player_id", "error"),
    [
        (
            SimpleNamespace(player_id=77, provider="api-football", provider_id="101"),
            99,
            "identity conflict",
        ),
        (
            SimpleNamespace(player_id=77, provider="api-football", provider_id=None),
            77,
            "none was found",
        ),
    ],
)
def test_existing_identity_conflicts_and_legacy_candidates_are_not_upserted(
    existing, resolved_player_id, error
):
    repository = PlayerRepository(existing)
    service = _player_sync(
        repository,
        IdentityResolver("RESOLVED_EXISTING", resolved_player_id),
    )

    with pytest.raises(RuntimeError, match=error):
        asyncio.run(service.upsert_player(None, {
            "provider": "api-football",
            "provider_id": "101",
            "photo": "https://img/player.png",
        }))

    assert repository.upsert_calls == []


class Cache:
    def __init__(self, value=None):
        self.values = {}
        if value is not None:
            self.values[make_lineup_cache_key(123)] = value
        self.set_calls = []

    async def get_json(self, key):
        return self.values.get(key)

    async def set_json(self, key, value, ttl):
        self.values[key] = value
        self.set_calls.append((key, value, ttl))


class QueryResult:
    def __init__(self, record):
        self.record = record

    def scalar_one_or_none(self):
        return self.record


class Database:
    def __init__(self, record=None):
        self.record = record

    async def execute(self, statement):
        return QueryResult(self.record)


class ReadPlayerService:
    def __init__(self, players):
        self.players = players
        self.batch_calls = []

    async def get_players_by_provider_ids(self, db, provider_ids):
        self.batch_calls.append(list(provider_ids))
        return [self.players[key] for key in provider_ids if key in self.players]


class NoProviderClient:
    def __init__(self):
        self.calls = []

    async def get(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        raise AssertionError("Lineup GET must not fetch provider data")


@pytest.mark.parametrize("cache_hit", [True, False], ids=["cache-hit", "database-fallback"])
def test_lineup_photo_precedence_is_consistent_and_does_not_mutate_source(cache_hit):
    raw = [{
        "team": {"id": 11},
        "startXI": [
            {"player": {
                "id": "fallback",
                "player_id": 201,
                "name": "Lineup Photo",
                "photo": "https://img/lineup.png",
            }},
            {"player": {
                "id": "orphan",
                "player_id": 204,
                "name": "Unmatched Player",
                "photo": "https://img/orphan.png",
            }},
            {"player": {"id": "none", "player_id": 202, "name": "No Photo", "photo": "  "}},
            {"player": {
                "id": "conflict",
                "player_id": 999,
                "name": "Identity Conflict",
                "photo": "https://img/lineup-conflict.png",
            }},
        ],
        "substitutes": [{
            "player": {
                "id": "master",
                "player_id": 203,
                "name": "Master Photo",
                "photo": "https://img/lineup-old.png",
            }
        }],
    }]
    original_raw = deepcopy(raw)
    record = MatchLineup(match_id=123, data=raw)
    db = Database(record=None if cache_hit else record)
    cache = Cache(raw if cache_hit else None)
    players = ReadPlayerService({
        "fallback": SimpleNamespace(player_id=201, provider_id="fallback", photo=None),
        "none": SimpleNamespace(player_id=202, provider_id="none", photo=None),
        "conflict": SimpleNamespace(
            player_id=300,
            provider_id="conflict",
            photo="https://img/wrong-player.png",
        ),
        "master": SimpleNamespace(
            player_id=203,
            provider_id="master",
            photo="https://img/master.png",
        ),
    })
    client = NoProviderClient()
    service = LineupService(
        client=client,
        cache_service=cache,
        player_service=players,
    )

    result = asyncio.run(service.get_cached_match_lineup(db, 123))

    starters = result[0]["startXI"]
    substitutes = result[0]["substitutes"]
    assert starters[0]["player"]["photo"] == "https://img/lineup.png"
    assert starters[1]["player"]["photo"] == "https://img/orphan.png"
    assert "photo" not in starters[2]["player"]
    assert starters[3]["player"]["photo"] == "https://img/lineup-conflict.png"
    assert substitutes[0]["player"]["photo"] == "https://img/master.png"
    assert raw == original_raw
    assert client.calls == []

    if cache_hit:
        assert cache.values[make_lineup_cache_key(123)] == original_raw
    else:
        assert cache.set_calls[0][1]["data"] == original_raw
