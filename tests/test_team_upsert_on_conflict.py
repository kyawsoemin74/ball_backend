import asyncio
from types import SimpleNamespace

from app.cache import make_cache_key
from app.repositories.team_repository import TeamRepository
from app.services import team_sync_service as team_sync_module
from app.services.team_sync_service import TeamSyncService
from app.services.team_sync_service import (
    _TEAM_POST_COMMIT_CACHE_KEYS,
    _clear_team_post_commit_cache_invalidation,
    _run_team_post_commit_cache_invalidation,
)


class ExecuteTrackingDB:
    def __init__(self):
        self.execute_calls = []
        self.flush_calls = 0

    async def execute(self, query):
        self.execute_calls.append(query)

        class FakeResult:
            def scalars(self):
                return self

            def all(self):
                return []

            def scalar_one_or_none(self):
                return None

        return FakeResult()

    async def flush(self):
        self.flush_calls += 1

    def add(self, _obj):
        raise AssertionError("TeamSyncService must not use db.add for persistence")

    def add_all(self, _objs):
        raise AssertionError("TeamSyncService must not use db.add_all for persistence")


class RecordingCacheService:
    def __init__(self):
        self.deleted = []

    def delete_sync(self, key):
        self.deleted.append(key)


class TrackingTeamRepository:
    def __init__(self, existing_ids=None):
        self.existing_ids = set(existing_ids or [])
        self.upsert_many_rows = []
        self.upsert_one_rows = []

    async def get_many_by_ids(self, db, team_ids):
        return [SimpleNamespace(team_id=tid) for tid in team_ids if tid in self.existing_ids]

    async def upsert_many(self, db, rows):
        self.upsert_many_rows.extend(rows)

    async def upsert_one(self, db, row):
        self.upsert_one_rows.append(dict(row))
        return SimpleNamespace(
            team_id=row["team_id"],
            name=row["name"],
            country=row.get("country"),
            logo=row.get("logo"),
            stadium=row.get("stadium"),
            founded=row.get("founded"),
        )


class FakeSessionWithInfo:
    def __init__(self):
        self.info = {}


class SyncSessionBackedDB:
    def __init__(self):
        self.sync_session = FakeSessionWithInfo()
        self.flush_calls = 0

    async def flush(self):
        self.flush_calls += 1


class TrackingTeamContextRepository:
    def __init__(self):
        self.update_calls = []

    async def get_by_id(self, db, team_id):
        return SimpleNamespace(team_id=team_id, current_league_id=10, current_season="2025")

    async def update_team_context(self, db, team_id, *, current_league_id=None, current_season=None):
        self.update_calls.append(
            {
                "team_id": team_id,
                "current_league_id": current_league_id,
                "current_season": current_season,
            }
        )


def test_team_repository_upsert_many_uses_real_primary_key_constraint():
    repository = TeamRepository()
    db = ExecuteTrackingDB()
    rows = [
        {"team_id": 1, "name": "One", "country": "X", "logo": None, "stadium": None, "founded": 1900},
        {"team_id": 2, "name": "Two", "country": "Y", "logo": None, "stadium": None, "founded": 1901},
    ]

    asyncio.run(repository.upsert_many(db, rows))

    sql_text = "\n".join(str(stmt) for stmt in db.execute_calls)
    assert "ON CONFLICT ON CONSTRAINT teams_pkey" in sql_text
    assert "DO UPDATE SET" in sql_text


def test_team_repository_upsert_one_uses_real_primary_key_constraint():
    repository = TeamRepository()

    class ExecuteTrackingDBWithSelect(ExecuteTrackingDB):
        async def execute(self, query):
            self.execute_calls.append(query)

            class FakeResult:
                def __init__(self, select_row=None):
                    self._select_row = select_row

                def scalars(self):
                    return self

                def all(self):
                    return []

                def scalar_one_or_none(self):
                    return self._select_row

            if "SELECT teams.team_id" in str(query):
                return FakeResult(select_row=SimpleNamespace(team_id=3))
            return FakeResult()

    db = ExecuteTrackingDBWithSelect()

    asyncio.run(
        repository.upsert_one(
            db,
            {"team_id": 3, "name": "Three", "country": "Z", "logo": None, "stadium": None, "founded": 1902},
        )
    )

    sql_text = "\n".join(str(stmt) for stmt in db.execute_calls)
    assert "ON CONFLICT ON CONSTRAINT teams_pkey" in sql_text
    assert "DO UPDATE SET" in sql_text


def test_ensure_teams_exist_upserts_only_missing_rows_and_deduplicates_payload():
    repository = TrackingTeamRepository(existing_ids={10})
    cache_service = RecordingCacheService()
    service = TeamSyncService(cache_service=cache_service, team_repository=repository)
    db = ExecuteTrackingDB()

    result = asyncio.run(
        service.ensure_teams_exist(
            db,
            [
                {"team_id": 10, "name": "Existing"},
                {"id": 11, "name": "New"},
                {"team_id": 11, "name": "Duplicate New"},
                {"team_id": None, "name": "Invalid"},
            ],
        )
    )

    assert result == {"created": 1, "existing": 1, "total": 2}
    assert len(repository.upsert_many_rows) == 1
    assert repository.upsert_many_rows[0]["team_id"] == 11
    assert db.flush_calls == 1


def test_upsert_team_routes_persistence_to_repository_and_invalidates_cache(monkeypatch):
    repository = TrackingTeamRepository()
    cache_service = RecordingCacheService()
    service = TeamSyncService(cache_service=cache_service, team_repository=repository)
    db = ExecuteTrackingDB()
    deleted_keys = []

    def fake_cache_delete_sync(key):
        deleted_keys.append(key)

    monkeypatch.setattr(team_sync_module, "cache_delete_sync", fake_cache_delete_sync)

    team = asyncio.run(
        service.upsert_team(
            db,
            {
                "team": {"id": 99, "name": "Ninety Nine", "country": "MM", "logo": None, "founded": 1999},
                "venue": {"name": "Home Ground"},
            },
        )
    )

    assert team.team_id == 99
    assert repository.upsert_one_rows == [
        {
            "team_id": 99,
            "name": "Ninety Nine",
            "country": "MM",
            "logo": None,
            "stadium": "Home Ground",
            "founded": 1999,
        }
    ]
    assert db.flush_calls == 1
    assert deleted_keys == [make_cache_key("team", 99)]


def test_upsert_team_queues_cache_invalidation_until_commit_when_sync_session_exists():
    repository = TrackingTeamRepository()
    cache_service = RecordingCacheService()
    service = TeamSyncService(cache_service=cache_service, team_repository=repository)
    db = SyncSessionBackedDB()

    asyncio.run(
        service.upsert_team(
            db,
            {
                "team": {"id": 77, "name": "Seventy Seven", "country": "MM", "logo": None, "founded": 1977},
                "venue": {"name": "Queue Ground"},
            },
        )
    )

    queued_keys = db.sync_session.info.get(_TEAM_POST_COMMIT_CACHE_KEYS)
    assert queued_keys == {make_cache_key("team", 77)}
    assert cache_service.deleted == []


def test_team_cache_invalidation_runs_only_after_commit(monkeypatch):
    deleted_keys = []

    def fake_cache_delete_sync(key):
        deleted_keys.append(key)

    monkeypatch.setattr(team_sync_module, "cache_delete_sync", fake_cache_delete_sync)

    session = FakeSessionWithInfo()
    cache_key = make_cache_key("team", 55)
    session.info[_TEAM_POST_COMMIT_CACHE_KEYS] = {cache_key}

    _run_team_post_commit_cache_invalidation(session)

    assert deleted_keys == [cache_key]
    assert _TEAM_POST_COMMIT_CACHE_KEYS not in session.info


def test_team_repository_update_team_context_allows_partial_updates():
    repository = TeamRepository()
    db = ExecuteTrackingDB()

    asyncio.run(repository.update_team_context(db, 5, current_league_id=39, current_season=None))
    asyncio.run(repository.update_team_context(db, 6, current_league_id=None, current_season="2026"))
    asyncio.run(repository.update_team_context(db, 7, current_league_id=None, current_season=None))

    assert len(db.execute_calls) == 2
    for stmt in db.execute_calls:
        assert stmt is not None


def test_team_sync_service_skips_repository_when_values_are_unchanged():
    repository = TrackingTeamContextRepository()
    cache_service = RecordingCacheService()
    service = TeamSyncService(cache_service=cache_service, team_repository=repository)
    db = ExecuteTrackingDB()

    asyncio.run(service.update_team_context(db, 1, current_league_id=10, current_season="2025"))

    assert repository.update_calls == []


def test_team_sync_service_delegates_repository_when_values_change():
    repository = TrackingTeamContextRepository()
    cache_service = RecordingCacheService()
    service = TeamSyncService(cache_service=cache_service, team_repository=repository)
    db = ExecuteTrackingDB()

    asyncio.run(service.update_team_context(db, 1, current_league_id=11, current_season="2026"))

    assert repository.update_calls == [{"team_id": 1, "current_league_id": 11, "current_season": "2026"}]


def test_team_sync_service_does_not_commit_flush_or_cache():
    repository = TrackingTeamContextRepository()
    cache_service = RecordingCacheService()
    service = TeamSyncService(cache_service=cache_service, team_repository=repository)
    db = ExecuteTrackingDB()

    asyncio.run(service.update_team_context(db, 1, current_league_id=11, current_season="2026"))

    assert not hasattr(db, "commit")
    assert db.flush_calls == 0
    assert cache_service.deleted == []


def test_team_cache_invalidation_cleared_on_rollback(monkeypatch):
    deleted_keys = []

    def fake_cache_delete_sync(key):
        deleted_keys.append(key)

    monkeypatch.setattr(team_sync_module, "cache_delete_sync", fake_cache_delete_sync)

    session = FakeSessionWithInfo()
    session.info[_TEAM_POST_COMMIT_CACHE_KEYS] = {make_cache_key("team", 56)}

    _clear_team_post_commit_cache_invalidation(session)
    _run_team_post_commit_cache_invalidation(session)

    assert deleted_keys == []
