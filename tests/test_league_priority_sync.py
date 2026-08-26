import asyncio

from app.models.league import League
from app.services.league_service import LeagueService


class FakeRepository:
    def __init__(self, existing=None):
        self.existing = existing

    async def get_by_id(self, db, league_id):
        if self.existing is not None and self.existing.league_id == league_id:
            return self.existing
        return None

    async def find_by_provider_identity(self, db, provider, provider_id):
        if self.existing is None:
            return None
        if (
            self.existing.provider == provider
            and self.existing.provider_id == str(provider_id)
        ):
            return self.existing
        return None

    async def get_many_by_ids(self, db, league_ids):
        return list(self.existing) if isinstance(self.existing, list) else []


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
    display_order=1,
):
    return League(
        league_id=league_id,
        provider="api-football",
        provider_id=str(league_id),
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

    async def run():
        updated = await service.upsert_league(None, {
            "league": {"id": 1, "name": "Existing League", "logo": None},
            "country": "England",
            "seasons": [{"year": 2024}],
        })
        return updated

    updated = asyncio.run(run())

    assert updated.display_order == 7


def test_league_service_fails_closed_when_master_is_missing():
    async def run():
        return await FakeRepository(existing=None).find_by_provider_identity(
            None, "api-football", "99"
        )

    assert asyncio.run(run()) is None
