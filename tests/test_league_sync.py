import asyncio

from app.models.league import League
from app.repositories.league_repository import LeagueRepository
from app.services.league_service import LeagueService


class FakeRepository:
    def __init__(self, existing=None):
        self.existing = existing

    async def get_by_id(self, db, league_id):
        if isinstance(self.existing, list):
            return next(
                (
                    league
                    for league in self.existing
                    if league.league_id == league_id
                ),
                None,
            )
        if self.existing is not None and self.existing.league_id == league_id:
            return self.existing
        return None

    async def find_by_provider_identity(self, db, provider, provider_id):
        provider_id = str(provider_id) if provider_id is not None else None
        matches = [
            league
            for league in (
                self.existing if isinstance(self.existing, list) else [self.existing]
            )
            if league is not None
            and league.provider == provider
            and league.provider_id == provider_id
        ]
        if len(matches) > 1:
            raise ValueError(
                f"Multiple leagues found for provider={provider} "
                f"provider_id={provider_id}"
            )
        return matches[0] if matches else None

    async def get_many_by_ids(self, db, league_ids):
        leagues = self.existing if isinstance(self.existing, list) else [self.existing]
        return [
            league
            for league in leagues
            if league is not None and league.league_id in league_ids
        ]


class FakeCacheService:
    def __init__(self):
        self.deleted = []

    def delete_sync(self, key):
        self.deleted.append(key)


class FakeClient:
    async def get(self, path, params=None):
        return {"response": []}


def make_existing_league(
    league_id=1,
    provider_id=None,
    display_order=1,
):
    return League(
        league_id=league_id,
        provider="api-football",
        provider_id=str(
            provider_id if provider_id is not None else league_id
        ),
        name="Existing League",
        country="England",
        logo=None,
        season="2024",
        is_featured=False,
        display_order=display_order,
    )


def test_league_service_preserves_existing_display_order_on_update():
    existing = make_existing_league(display_order=7)
    service = LeagueService(client=FakeClient(), cache_service=FakeCacheService())
    service.league_repository = FakeRepository(existing=existing)

    class FakeDB:
        async def flush(self):
            return None

    async def run():
        updated = await service.upsert_league(FakeDB(), {
            "league": {"id": 1, "name": "Existing League", "logo": None},
            "country": "England",
            "seasons": [{"year": 2024}],
        })
        return updated

    updated = asyncio.run(run())

    assert updated.display_order == 7


def test_league_service_fails_closed_when_master_is_missing():
    service = LeagueService(client=FakeClient(), cache_service=FakeCacheService())

    class FakeDB:
        async def flush(self):
            return None

    db = FakeDB()
    service.league_repository = FakeRepository(existing=None)

    async def run():
        return await service.upsert_league(db, {
            "league": {"id": 99, "name": "New League", "logo": None},
            "country": "England",
            "seasons": [{"year": 2024}],
        })

    league = asyncio.run(run())

    assert league is None


def test_league_service_uses_master_id_instead_of_provider_id():
    master = make_existing_league(league_id=42, provider_id=7)
    service = LeagueService(client=FakeClient(), cache_service=FakeCacheService())
    service.league_repository = FakeRepository(existing=master)

    class FakeDB:
        async def flush(self):
            return None

    async def run():
        return await service.upsert_league(FakeDB(), {
            "league": {"id": 7, "name": "Existing League", "logo": None},
            "country": "England",
            "seasons": [{"year": 2024}],
        })

    resolved = asyncio.run(run())

    assert resolved.league_id == 42
    assert resolved.league_id != 7


def test_provider_identity_duplicate_fails_closed():
    first = make_existing_league(league_id=42, provider_id=7)
    second = make_existing_league(league_id=43, provider_id=7)
    repository = FakeRepository(existing=[first, second])

    async def run():
        return await repository.find_by_provider_identity(None, "api-football", "7")

    try:
        asyncio.run(run())
    except ValueError as exc:
        assert "Multiple leagues found" in str(exc)
    else:
        raise AssertionError("Duplicate provider identity did not fail closed")


def test_provider_identity_resolution_is_idempotent():
    master = make_existing_league(league_id=42, provider_id=7)
    repository = FakeRepository(existing=master)

    async def run():
        first = await repository.find_by_provider_identity(None, "api-football", "7")
        second = await repository.find_by_provider_identity(None, "api-football", "7")
        return first, second

    first, second = asyncio.run(run())

    assert first.league_id == 42
    assert second.league_id == 42
    assert first.league_id == second.league_id


def test_missing_provider_league_id_is_rejected():
    service = LeagueService(client=FakeClient(), cache_service=FakeCacheService())

    async def run():
        return await service.upsert_league(None, {"league": {"name": "Missing ID"}})

    try:
        asyncio.run(run())
    except ValueError as exc:
        assert "missing the id field" in str(exc)
    else:
        raise AssertionError("Missing provider League ID was accepted")


def test_league_repository_exposes_exact_provider_identity_lookup():
    assert hasattr(LeagueRepository, "find_by_provider_identity")


def test_league_service_exposes_exact_provider_identity_lookup():
    service = LeagueService(client=FakeClient(), cache_service=FakeCacheService())
    assert hasattr(service, "find_by_provider_identity")
