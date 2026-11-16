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
    ("existing_photo", "incoming_photo", "expected_photo", "normalized_photo"),
    [
        ("https://img/old.png", None, "https://img/old.png", None),
        ("https://img/old.png", "https://img/new.png", "https://img/new.png", "https://img/new.png"),
        ("https://img/old.png", "http://img/new.png", "http://img/new.png", "http://img/new.png"),
        ("https://img/old.png", "", "https://img/old.png", None),
        ("https://img/old.png", "  ", "https://img/old.png", None),
        ("https://img/old.png", "invalid-url", "https://img/old.png", None),
        ("https://img/old.png", "https://", "https://img/old.png", None),
        ("https://img/old.png", "ftp://img/player.png", "https://img/old.png", None),
    ],
)
def test_existing_photo_uses_null_preserving_upsert(
    existing_photo, incoming_photo, expected_photo, normalized_photo
):
    existing = SimpleNamespace(
        player_id=77,
        provider="api-football",
        provider_id="101",
        photo=existing_photo,
        name="Existing Player",
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
    assert repository.upsert_calls[0]["photo"] == normalized_photo


def test_existing_non_null_fields_survive_incoming_null_values():
    existing = SimpleNamespace(
        player_id=77,
        provider="api-football",
        provider_id="101",
        photo="https://img/old.png",
        name="Existing Player",
        nationality="Brazil",
        position="Defender",
        first_name="Existing",
    )
    repository = PlayerRepository(existing)
    service = _player_sync(repository, IdentityResolver("RESOLVED_EXISTING", 77))

    asyncio.run(service.upsert_player(None, {
        "provider": "api-football",
        "provider_id": "101",
        "photo": "javascript:alert(1)",
        "name": None,
        "nationality": "  ",
        "position": "F",
        "first_name": " ",
        "birth_place": "",
    }))

    assert existing.photo == "https://img/old.png"
    assert existing.name == "Existing Player"
    assert existing.nationality == "Brazil"
    assert existing.position == "Defender"
    assert existing.first_name == "Existing"
    assert repository.upsert_calls[0]["name"] == "Existing Player"
    assert repository.upsert_calls[0]["nationality"] is None
    assert repository.upsert_calls[0]["position"] is None
    assert repository.upsert_calls[0]["first_name"] is None
    assert repository.upsert_calls[0]["birth_place"] is None


def test_new_player_is_created_with_photo():
    repository = PlayerRepository()
    service = _player_sync(repository, IdentityResolver("CREATE_NEW"))

    result = asyncio.run(service.upsert_player(None, {
        "provider": "api-football",
        "provider_id": "101",
        "name": "New Player",
        "photo": "https://img/player.png",
    }))

    assert result.photo == "https://img/player.png"
    assert len(repository.upsert_calls) == 1


@pytest.mark.parametrize(
    "incoming_photo",
    [None, "", "  ", "invalid-url", "ftp://img/player.png", "javascript:alert(1)"],
)
def test_new_player_does_not_store_invalid_photo(incoming_photo):
    repository = PlayerRepository()
    service = _player_sync(repository, IdentityResolver("CREATE_NEW"))

    asyncio.run(service.upsert_player(None, {
        "provider": "api-football",
        "provider_id": "101",
        "name": "New Player",
        "photo": incoming_photo,
    }))

    assert repository.upsert_calls[0]["photo"] is None


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
        }, expected_local_player_id=resolved_player_id))

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
    def __init__(self, players, local_players=None):
        self.players = players
        self.batch_calls = []
        self.local_batch_calls = []
        self.local_players = (
            list(local_players) if local_players is not None else list(players.values())
        )

    async def get_players_by_provider_ids(self, db, provider_ids):
        self.batch_calls.append(list(provider_ids))
        return [self.players[key] for key in provider_ids if key in self.players]

    async def get_players_by_ids(self, db, player_ids):
        self.local_batch_calls.append(list(player_ids))
        return [
            player
            for player in self.local_players
            if player.player_id in player_ids
        ]


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
    assert substitutes[0]["player"]["photo"] == "https://img/lineup-old.png"
    assert raw == original_raw
    assert client.calls == []

    if cache_hit:
        assert cache.values[make_lineup_cache_key(123)] == original_raw
    else:
        assert cache.set_calls[0][1]["data"] == original_raw


def _read_single_lineup_player(player_data, player_service):
    raw = [{
        "team": {"id": 11},
        "startXI": [{"player": player_data}],
        "substitutes": [],
    }]
    cache = Cache(raw)
    service = LineupService(
        client=NoProviderClient(),
        cache_service=cache,
        player_service=player_service,
    )
    result = asyncio.run(service.get_cached_match_lineup(Database(), 123))
    return result[0]["startXI"][0]["player"]


@pytest.mark.parametrize(
    "lineup_photo",
    ["https://lineup.example/photo.png", "http://lineup.example/photo.png"],
)
def test_valid_lineup_photo_precedes_valid_player_master_photo(lineup_photo):
    master = SimpleNamespace(
        player_id=201,
        provider="api-football",
        provider_id="101",
        photo="https://master.example/photo.png",
    )
    players = ReadPlayerService({"101": master})

    player = _read_single_lineup_player(
        {
            "id": "101",
            "player_id": 201,
            "name": "Player",
            "photo": lineup_photo,
        },
        players,
    )

    assert player["photo"] == lineup_photo
    assert players.batch_calls == []


@pytest.mark.parametrize(
    "lineup_photo",
    [
        None,
        "",
        "   ",
        "not-a-url",
        "ftp://lineup.example/photo.png",
        "https://",
        123,
    ],
)
def test_invalid_lineup_photo_falls_back_to_valid_exact_master_photo(lineup_photo):
    master = SimpleNamespace(
        player_id=201,
        provider="api-football",
        provider_id="101",
        photo="https://master.example/photo.png",
    )
    players = ReadPlayerService({"101": master})

    player = _read_single_lineup_player(
        {
            "id": "101",
            "player_id": 201,
            "name": "Player",
            "photo": lineup_photo,
        },
        players,
    )

    assert player["photo"] == "https://master.example/photo.png"
    assert players.batch_calls == [["101"]]


@pytest.mark.parametrize(
    "master_photo",
    [None, "", "   ", "malformed", "ftp://master.example/p.png", "https://", 7],
)
def test_invalid_photos_from_both_sources_are_omitted(master_photo):
    master = SimpleNamespace(
        player_id=201,
        provider="api-football",
        provider_id="101",
        photo=master_photo,
    )
    players = ReadPlayerService({"101": master})

    player = _read_single_lineup_player(
        {
            "id": "101",
            "player_id": 201,
            "name": "Player",
            "photo": "javascript:alert(1)",
        },
        players,
    )

    assert player.get("photo") is None


def test_local_id_fallback_requires_matching_provider_identity():
    master = SimpleNamespace(
        player_id=201,
        provider="api-football",
        provider_id="101",
        photo="https://master.example/photo.png",
    )
    players = ReadPlayerService({}, local_players=[master])

    player = _read_single_lineup_player(
        {
            "provider_id": "101",
            "local_player_id": 201,
            "name": "Player",
        },
        players,
    )

    assert player["photo"] == "https://master.example/photo.png"
    assert players.batch_calls == [["101"]]
    assert players.local_batch_calls == [[201]]


def test_local_id_without_provider_identity_does_not_receive_master_photo():
    legacy_player = SimpleNamespace(
        player_id=201,
        provider="api-football",
        provider_id=None,
        photo="https://master.example/wrong-player.png",
    )
    players = ReadPlayerService({}, local_players=[legacy_player])

    player = _read_single_lineup_player(
        {"local_player_id": 201, "name": "Legacy Player"},
        players,
    )

    assert player.get("photo") is None
    assert players.batch_calls == []
    assert players.local_batch_calls == []


def test_local_id_with_provider_mismatch_does_not_receive_master_photo():
    another_player = SimpleNamespace(
        player_id=201,
        provider="api-football",
        provider_id="another-id",
        photo="https://master.example/wrong-player.png",
    )
    players = ReadPlayerService({}, local_players=[another_player])

    player = _read_single_lineup_player(
        {
            "provider_id": "101",
            "local_player_id": 201,
            "name": "Player",
        },
        players,
    )

    assert player.get("photo") is None


def test_conflicting_provider_ids_do_not_receive_master_photo():
    master = SimpleNamespace(
        player_id=201,
        provider="api-football",
        provider_id="101",
        photo="https://master.example/wrong-player.png",
    )
    players = ReadPlayerService({"101": master}, local_players=[master])

    player = _read_single_lineup_player(
        {
            "provider_player_id": "101",
            "provider_id": "202",
            "local_player_id": 201,
            "name": "Conflicting Identity",
            "photo": "javascript:alert(1)",
        },
        players,
    )

    assert player.get("photo") is None
    assert players.batch_calls == []
    assert players.local_batch_calls == []


def test_duplicate_provider_master_rows_do_not_select_a_photo():
    class DuplicateProviderRows(ReadPlayerService):
        async def get_players_by_provider_ids(self, db, provider_ids):
            self.batch_calls.append(list(provider_ids))
            return [
                SimpleNamespace(
                    player_id=201,
                    provider="api-football",
                    provider_id="101",
                    photo="https://master.example/first.png",
                ),
                SimpleNamespace(
                    player_id=202,
                    provider="api-football",
                    provider_id="101",
                    photo="https://master.example/second.png",
                ),
            ]

    players = DuplicateProviderRows({})
    player = _read_single_lineup_player(
        {
            "id": "101",
            "player_id": 201,
            "name": "Ambiguous Player",
            "photo": "javascript:alert(1)",
        },
        players,
    )

    assert player.get("photo") is None
    assert players.batch_calls == [["101"]]
    assert players.local_batch_calls == []
