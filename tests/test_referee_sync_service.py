import asyncio

from app.services.referee_sync_service import RefereeSyncService


class FakeDB:
    pass


class FakeRefereeRepository:
    def __init__(self):
        self.rows = []

    async def get_by_normalized_name(self, db, normalized_name, provider="api-football"):
        for row in self.rows:
            if row["provider"] == provider and row["normalized_name"] == normalized_name:
                return row
        return None

    async def create(self, db, row):
        row = dict(row)
        row["referee_id"] = len(self.rows) + 1
        self.rows.append(row)
        return type("CreatedReferee", (), row)()

    async def update(self, db, referee_id, values):
        for row in self.rows:
            if row["referee_id"] == referee_id:
                row.update(values)
                return row
        raise KeyError(referee_id)


def test_referee_sync_service_normalizes_fixture_name():
    service = RefereeSyncService(FakeRefereeRepository())

    record = service.normalize_referee_name("  Hélder   Malheiro  ")

    assert record["provider"] == "api-football"
    assert record["provider_id"] is None
    assert record["name"] == "Hélder Malheiro"
    assert record["normalized_name"] == "helder malheiro"
    assert record["nationality"] is None
    assert record["photo"] is None


def test_referee_sync_service_reuses_existing_canonical_record_by_normalized_name():
    repo = FakeRefereeRepository()
    service = RefereeSyncService(repo)
    db = FakeDB()

    first = asyncio.run(service.sync_fixture_referee(db, "  M. Radina  "))
    second = asyncio.run(service.sync_fixture_referee(db, "M. Radina"))

    assert first["referee_id"] == second["referee_id"]
    assert first["name"] == "M. Radina"
    assert len(repo.rows) == 1
    assert repo.rows[0]["normalized_name"] == "m. radina"
