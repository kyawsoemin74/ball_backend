import asyncio

from app.services.coach_sync_service import CoachSyncService


class FakeDB:
    pass


class FakeCoachRepository:
    def __init__(self):
        self.rows = []

    async def get_by_provider_id(self, db, provider_id, provider="api-football"):
        for row in self.rows:
            if row["provider"] == provider and row["provider_id"] == provider_id:
                return row
        return None

    async def get_by_normalized_name(self, db, normalized_name, provider="api-football"):
        for row in self.rows:
            if row["provider"] == provider and row["normalized_name"] == normalized_name:
                return row
        return None

    async def create(self, db, row):
        row = dict(row)
        row["coach_id"] = len(self.rows) + 1
        self.rows.append(row)
        return type("CreatedCoach", (), row)()

    async def update(self, db, coach_id, values):
        for row in self.rows:
            if row["coach_id"] == coach_id:
                row.update(values)
                return row
        raise KeyError(coach_id)


def test_coach_sync_service_normalizes_name_and_sets_provider_id():
    service = CoachSyncService(FakeCoachRepository())

    record = service.normalize_coach_name("  Jorge Sampaoli  ")

    assert record["provider"] == "api-football"
    assert record["provider_id"] is None
    assert record["name"] == "Jorge Sampaoli"
    assert record["normalized_name"] == "jorge sampaoli"
    assert record["nationality"] is None
    assert record["photo"] is None


def test_coach_sync_service_reuses_existing_canonical_record_by_provider_id_or_name():
    repo = FakeCoachRepository()
    service = CoachSyncService(repo)
    db = FakeDB()

    first = asyncio.run(service.sync_team_coach(db, {"id": 129, "name": "  Rogério Ceni  ", "nationality": "Brazil", "photo": "https://example.com/coach.png"}))
    second = asyncio.run(service.sync_team_coach(db, {"id": 129, "name": "Rogério Ceni", "nationality": "Brazil", "photo": "https://example.com/coach.png"}))

    assert first["coach_id"] == second["coach_id"]
    assert first["provider_id"] == "129"
    assert first["name"] == "Rogério Ceni"
    assert len(repo.rows) == 1
    assert repo.rows[0]["normalized_name"] == "rogerio ceni"
