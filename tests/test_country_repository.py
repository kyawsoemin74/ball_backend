import asyncio
from types import SimpleNamespace

from app.models.country import Country
from app.repositories.country_repository import CountryRepository
from app.services.league_sync_service import LeagueSyncService


class FakeCountryResult:
    def __init__(self, country):
        self._country = country

    def scalars(self):
        return self

    def one_or_none(self):
        return self._country

    def scalar_one_or_none(self):
        raise AssertionError("Country repository must use the SQLAlchemy scalars() API")


class FakeCountryDB:
    def __init__(self):
        self.executed = []
        self.added = []

    async def execute(self, statement):
        self.executed.append(statement)
        return FakeCountryResult(Country(country_id=1, name="Myanmar", code="MM", flag="https://example.com/mm.png"))

    def add(self, instance):
        self.added.append(instance)


def test_country_repository_upserts_country_record():
    db = FakeCountryDB()
    repo = CountryRepository()

    async def run():
        country = await repo.upsert_one(
            db,
            {
                "country_id": 1,
                "name": "Myanmar",
                "code": "MM",
                "flag": "https://example.com/mm.png",
            },
        )

        assert isinstance(country, Country)
        assert country.name == "Myanmar"
        assert country.code == "MM"

    asyncio.run(run())


def test_country_sync_updates_league_country_id():
    class FakeCountrySyncService:
        async def sync_from_league_payload(self, db, league_data):
            return {"status": "created", "country": {"country_id": 42, "name": "England"}}

    class FakeLeagueRepository:
        def __init__(self):
            self.calls = []

        async def find_by_provider_identity(self, db, provider, provider_id):
            return SimpleNamespace(league_id=1, provider=provider, provider_id=str(provider_id), name="Premier League")

        async def update_country_id(self, db, league_id, country_id):
            self.calls.append((league_id, country_id))

    class FakeAllowedLeagueRepository:
        async def get_allowed_ids(self, db):
            return {1}

    class FakeCacheService:
        def delete_sync(self, *args, **kwargs):
            return None

    async def run():
        fake_repository = FakeLeagueRepository()
        service = LeagueSyncService(
            cache_service=FakeCacheService(),
            league_repository=fake_repository,
            allowed_league_repository=FakeAllowedLeagueRepository(),
            fetch_all_leagues=None,
        )
        service.country_sync_service = FakeCountrySyncService()

        await service._upsert_league(
            db=SimpleNamespace(),
            league_data={"league": {"id": 100, "name": "Premier League", "country": "England"}},
            master=SimpleNamespace(league_id=1, provider="api-football", provider_id="100", name="Premier League"),
        )

        assert fake_repository.calls == [(1, 42)]

    asyncio.run(run())
