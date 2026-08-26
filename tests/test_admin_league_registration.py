import asyncio
from types import SimpleNamespace

import pytest

from app.services.league_service import LeagueService


class FakeProvider:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def get_league_details(self, provider_id):
        self.calls.append(provider_id)
        return self.response


class FakeLeagueRepository:
    def __init__(self, existing=None, next_id=100):
        self.existing = existing
        self.next_id = next_id
        self.created = []

    async def find_by_provider_identity(self, db, provider, provider_id):
        if self.existing and self.existing.provider == provider and self.existing.provider_id == str(provider_id):
            return self.existing
        return None

    async def get_by_id(self, db, league_id):
        return None

    async def create_registered(self, db, row):
        league = SimpleNamespace(**row)
        self.created.append(league)
        return league


class FakeCache:
    async def delete(self, key):
        return None


class FakeClient:
    async def get(self, path, params=None):
        return None


def make_service(response, existing=None):
    service = LeagueService(
        client=FakeClient(),
        cache_service=FakeCache(),
        league_provider=FakeProvider(response),
    )
    repository = FakeLeagueRepository(existing=existing)
    service.league_repository = repository
    return service, repository


class FakeDB:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


def provider_response(provider_id=140):
    return {
        "response": [{
            "league": {
                "id": provider_id,
                "name": "La Liga",
                "logo": "https://example.test/logo.png",
                "type": "League",
            },
            "country": {"name": "Spain", "code": "ES"},
        }]
    }


def test_register_league_validates_provider_and_creates_local_master_without_authorizing():
    service, repository = make_service(provider_response(), existing=None)

    league, created = asyncio.run(service.register_league(FakeDB(), "api-football", 140))

    assert created is True
    assert league.league_id == 140
    assert league.provider == "api-football"
    assert league.provider_id == "140"
    assert league.name == "La Liga"
    assert league.country == "Spain"
    assert league.country_code == "ES"
    assert league.logo.endswith("logo.png")
    assert league.type == "League"
    assert league.is_featured is False
    assert league.display_order == 999
    assert repository.created


def test_register_league_rejects_canonical_local_id_collision():
    service, repository = make_service(provider_response(), existing=None)
    collision = SimpleNamespace(league_id=140, provider="other", provider_id="140")
    repository.get_by_id = lambda db, league_id: asyncio.sleep(0, result=collision)

    with pytest.raises(ValueError, match="occupied"):
        asyncio.run(service.register_league(FakeDB(), "api-football", 140))

    assert repository.created == []


def test_register_league_is_idempotent_for_existing_provider_identity():
    existing = SimpleNamespace(league_id=12, provider="api-football", provider_id="140")
    service, repository = make_service(provider_response(), existing=existing)

    league, created = asyncio.run(service.register_league(FakeDB(), "api-football", 140))

    assert created is False
    assert league is existing
    assert repository.created == []


def test_register_league_rejects_unsupported_or_invalid_identity():
    service, repository = make_service(provider_response())

    with pytest.raises(ValueError, match="Unsupported"):
        asyncio.run(service.register_league(FakeDB(), "other", 140))
    with pytest.raises(ValueError, match="positive"):
        asyncio.run(service.register_league(FakeDB(), "api-football", 0))

    assert repository.created == []


def test_register_league_rejects_provider_not_found_and_identity_mismatch():
    missing_service, missing_repository = make_service({"response": []})
    with pytest.raises(LookupError, match="not found"):
        asyncio.run(missing_service.register_league(FakeDB(), "api-football", 140))
    assert missing_repository.created == []

    mismatch_service, mismatch_repository = make_service(provider_response(provider_id=141))
    with pytest.raises(ValueError, match="identity"):
        asyncio.run(mismatch_service.register_league(FakeDB(), "api-football", 140))
    assert mismatch_repository.created == []


def test_register_league_reports_provider_unavailable():
    service, repository = make_service(None)

    with pytest.raises(ConnectionError, match="unavailable"):
        asyncio.run(service.register_league(FakeDB(), "api-football", 140))

    assert repository.created == []