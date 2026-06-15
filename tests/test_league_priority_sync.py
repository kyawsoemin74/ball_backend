import asyncio

from app.models.league import League
from app.services.league_service import LeagueService


class FakeRepository:
    def __init__(self, existing=None):
        self.existing = existing

    async def get_by_id(self, db, league_id):
        return self.existing

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


def make_existing_league(league_id=1, display_order=1):
    return League(
        league_id=league_id,
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


def test_league_service_defaults_new_leagues_to_display_order_999():
    service = LeagueService(client=FakeClient(), cache_service=FakeCacheService())

    class FakeDB:
        async def flush(self):
            return None

        async def refresh(self, obj):
            return None

        def add(self, obj):
            self.added = obj

    db = FakeDB()
    service.league_repository = FakeRepository(existing=None)

    async def run():
        league = await service.upsert_league(db, {
            "league": {"id": 99, "name": "New League", "logo": None},
            "country": "England",
            "seasons": [{"year": 2024}],
        })
        return league

    league = asyncio.run(run())

    assert league.display_order == 999
