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
    def __init__(self, existing_ids=None, provider_ids=None):
        self.existing_ids = set(existing_ids or [])
        self.provider_ids = dict(provider_ids or {})
        self.upsert_many_rows = []
        self.upsert_one_rows = []

    async def find_by_provider_identity(self, db, provider, provider_id):
        team_id = self.provider_ids.get((provider, str(provider_id)))
        if team_id is None:
            return None
        return SimpleNamespace(team_id=team_id)

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


class DuplicateProviderRepository(TrackingTeamRepository):
    async def find_by_provider_identity(self, db, provider, provider_id):
        raise ValueError(f"Multiple teams found for provider={provider} provider_id={provider_id}")

class CoachAssignmentRepository:
    def __init__(self, provider_id="10", coach_id=None):
        self.team = SimpleNamespace(team_id=101, provider_id=provider_id, coach_id=coach_id)
        self.updated = []

    async def get_by_id(self, db, team_id):
        return self.team if team_id == self.team.team_id else None

    async def update_current_coach(self, db, team_id, coach_id):
        self.updated.append((team_id, coach_id))
        self.team.coach_id = coach_id


class CoachAssignmentProvider:
    def __init__(self, payload):
        self.payload = payload

    async def get_team_coach(self, team_id):
        return self.payload


class CoachAssignmentSyncService:
    def __init__(self, payload):
        self.provider = CoachAssignmentProvider(payload)

    async def sync_team_coach(self, db, payload):
        return {"coach_id": payload["id"]}


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


def test_ensure_teams_exist_resolves_existing_masters_and_reports_missing():
    repository = TrackingTeamRepository(provider_ids={("api-football", "10"): 101})
    cache_service = RecordingCacheService()
    service = TeamSyncService(cache_service=cache_service, team_repository=repository)
    db = ExecuteTrackingDB()

    result = asyncio.run(
        service.ensure_teams_exist(
            db,
            [
                {"provider_id": 10, "name": "Existing"},
                {"provider_id": 11, "name": "Missing"},
                {"provider_id": None, "name": "Invalid"},
            ],
        )
    )

    assert result == {"created": 0, "existing": 1, "unresolved": 2, "total": 3}
    assert repository.upsert_many_rows == []
    assert db.flush_calls == 0


def test_provider_identity_resolves_to_different_local_team_id():
    repository = TrackingTeamRepository(provider_ids={("api-football", "99"): 7001})
    service = TeamSyncService(cache_service=RecordingCacheService(), team_repository=repository)

    result = asyncio.run(
        service.resolve_provider_teams(
            ExecuteTrackingDB(),
            [{"provider_id": 99, "name": "Provider Team"}],
        )
    )

    assert result == {"resolved": {99: 7001}, "unresolved": [], "total": 1}


def test_duplicate_provider_identity_fails_closed():
    service = TeamSyncService(
        cache_service=RecordingCacheService(),
        team_repository=DuplicateProviderRepository(),
    )

    try:
        asyncio.run(
            service.resolve_provider_teams(
                ExecuteTrackingDB(),
                [{"provider_id": 99, "name": "Ambiguous Team"}],
            )
        )
    except ValueError as exc:
        assert "Multiple teams found" in str(exc)
    else:
        raise AssertionError("Duplicate provider identity did not fail closed")


def test_upsert_team_resolves_existing_master_without_persistence(monkeypatch):
    repository = TrackingTeamRepository(provider_ids={("api-football", "99"): 1001})
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

    assert team.team_id == 1001
    assert repository.upsert_one_rows == []
    assert db.flush_calls == 0
    assert deleted_keys == []


def test_upsert_team_does_not_create_missing_master_or_invalidate_cache():
    repository = TrackingTeamRepository()
    cache_service = RecordingCacheService()
    service = TeamSyncService(cache_service=cache_service, team_repository=repository)
    db = SyncSessionBackedDB()

    result = asyncio.run(
        service.upsert_team(
            db,
            {
                "team": {"id": 77, "name": "Seventy Seven", "country": "MM", "logo": None, "founded": 1977},
                "venue": {"name": "Queue Ground"},
            },
        )
    )

    assert result is None
    assert repository.upsert_one_rows == []
    assert db.sync_session.info.get(_TEAM_POST_COMMIT_CACHE_KEYS) is None
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


def test_sync_team_coach_assigns_local_coach_id_and_invalidates_team_cache():
    repository = CoachAssignmentRepository()
    service = TeamSyncService(
        cache_service=RecordingCacheService(),
        team_repository=repository,
        coach_sync_service=CoachAssignmentSyncService({"id": 129, "name": "Coach A"}),
    )
    db = SyncSessionBackedDB()

    result = asyncio.run(service.sync_team_coach(db, 101))

    assert result == {"success": True, "team_id": 101, "coach_id": 129, "updated": True}
    assert repository.updated == [(101, 129)]
    assert make_cache_key("team", 101) in db.sync_session.info[_TEAM_POST_COMMIT_CACHE_KEYS]


def test_sync_team_coach_does_not_clear_assignment_when_provider_is_unavailable():
    repository = CoachAssignmentRepository(coach_id=129)
    service = TeamSyncService(
        cache_service=RecordingCacheService(),
        team_repository=repository,
        coach_sync_service=CoachAssignmentSyncService(None),
    )

    result = asyncio.run(service.sync_team_coach(ExecuteTrackingDB(), 101))

    assert result["reason"] == "coach_unavailable"
    assert repository.updated == []
