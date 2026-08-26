import asyncio

from app.models.country import Country
from app.repositories.country_repository import CountryRepository


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
