import asyncio
from contextlib import asynccontextmanager

from sqlalchemy.exc import IntegrityError

from app.services.fixture_sync_service import FixtureSyncService
from app.services.venue_sync_service import VenueSyncService


class FakeVenueRepository:
    def __init__(self, existing=None):
        self.existing = existing
        self.calls = []

    async def get_by_provider_id(self, db, provider_id, provider="api-football"):
        if self.existing and self.existing.provider == provider and self.existing.provider_id == provider_id:
            return self.existing
        return None

    async def upsert_one(self, db, venue_data):
        self.calls.append(dict(venue_data))
        if self.existing is None:
            self.existing = type("VenueRow", (), {"venue_id": 7, **venue_data})()
        else:
            self.existing.name = venue_data["name"]
            self.existing.city = venue_data.get("city")
        return self.existing


class BulkVenueRepository:
    def __init__(self, conflict_ids=None, error_ids=None):
        self.rows = {}
        self.conflict_ids = set(conflict_ids or [])
        self.error_ids = set(error_ids or [])
        self.attempts = {}

    async def get_by_provider_id(self, db, provider_id, provider="api-football"):
        return self.rows.get((provider, provider_id))

    async def upsert_one(self, db, venue_data):
        key = (venue_data.get("provider", "api-football"), venue_data["provider_id"])
        self.attempts[key] = self.attempts.get(key, 0) + 1
        if key[1] in self.error_ids:
            raise RuntimeError("unexpected venue error")
        if key[1] in self.conflict_ids and self.attempts[key] == 1:
            self.rows[key] = type("VenueRow", (), {"venue_id": len(self.rows) + 1, **venue_data})()
            raise IntegrityError("duplicate", None, None)
        row = self.rows.get(key)
        if row is None:
            row = type("VenueRow", (), {"venue_id": len(self.rows) + 1, **venue_data})()
            self.rows[key] = row
        return row


class FakeDB:
    def __init__(self):
        self.commit_calls = 0
        self.rollback_calls = 0
        self.savepoint_rollbacks = 0
        self.savepoint_releases = 0

    async def execute(self, query):
        class FakeScalars:
            def all(self):
                return []

        class FakeResult:
            def scalars(self):
                return FakeScalars()

            def scalar_one_or_none(self):
                return None

            def all(self):
                return []

        return FakeResult()

    async def flush(self):
        return None

    async def commit(self):
        self.commit_calls += 1

    async def rollback(self):
        self.rollback_calls += 1

    @asynccontextmanager
    async def begin_nested(self):
        try:
            yield self
        except IntegrityError:
            self.savepoint_rollbacks += 1
            raise
        else:
            self.savepoint_releases += 1


class FakeTeamService:
    async def resolve_provider_teams(self, db, teams):
        return {"resolved": {int(item["provider_id"]): int(item["provider_id"]) for item in teams}, "unresolved": [], "total": len(teams)}


class FakeCacheService:
    async def delete(self, key):
        return None


class FakeStandingService:
    class _Repository:
        async def get_for_league_season(self, db, league_id, season):
            return []

    def __init__(self):
        self.standing_repository = self._Repository()


class FakeVenueSyncService:
    def __init__(self):
        self.payloads = []

    async def sync_fixture_payload(self, db, payload):
        self.payloads.append(payload)
        return {"success": True, "venue_id": 7 if payload else None}


class FakeRefereeSyncService:
    def __init__(self):
        self.names = []

    async def sync_fixture_referee(self, db, referee_name):
        self.names.append(referee_name)
        return {"referee_id": 11}


class FakeAllowedLeagueRepository:
    async def get_allowed_ids(self, db):
        return {39}


class FakeLeagueRepository:
    async def find_by_provider_identity(self, db, provider, provider_id):
        return type("LeagueRow", (), {"league_id": 39})()


class FakeMatchRepository:
    async def get_many_by_ids(self, db, match_ids):
        return []


def test_fixture_venue_normalization_requires_real_positive_provider_id():
    service = VenueSyncService()

    assert service.normalize_fixture_venue({"id": 42, "name": "Arena", "city": "City"})["provider_id"] == "42"
    assert service.normalize_fixture_venue({"id": 0, "name": "Arena"}) is None
    assert service.normalize_fixture_venue({"id": "", "name": "Arena"}) is None
    assert service.normalize_fixture_venue({"id": "fake", "name": "Arena"}) is None


def test_fixture_payload_sync_updates_metadata_without_identity_fields():
    row = type("VenueRow", (), {"venue_id": 3, "provider": "api-football", "provider_id": "42", "name": "Old", "city": "Old City"})()
    repository = FakeVenueRepository(row)
    service = VenueSyncService()
    service.repository = repository

    result = asyncio.run(service.sync_fixture_payload(FakeDB(), {"id": 42, "name": "New", "city": "New City"}))

    assert result["venue_id"] == 3
    assert repository.calls == [{
        "provider": "api-football",
        "provider_id": "42",
        "name": "New",
        "city": "New City",
        "country": None,
        "country_code": None,
        "capacity": None,
        "surface": None,
        "image": None,
    }]
    assert row.provider_id == "42"


def test_missing_or_invalid_fixture_venue_is_nonfatal():
    service = VenueSyncService()
    repository = FakeVenueRepository()
    service.repository = repository
    db = FakeDB()

    missing = asyncio.run(service.sync_fixture_payload(db, None))
    invalid = asyncio.run(service.sync_fixture_payload(db, {"id": "bad", "name": "Arena"}))

    assert missing == {"success": True, "venue_id": None, "reason": "missing"}
    assert invalid == {"success": True, "venue_id": None, "reason": "invalid"}
    assert repository.calls == []


def _venue(provider_id):
    return {
        "provider": "api-football",
        "provider_id": str(provider_id),
        "name": f"Arena {provider_id}",
    }


def test_ensure_venues_exist_creates_new_venue_in_savepoint():
    service = VenueSyncService()
    repository = BulkVenueRepository()
    service.repository = repository
    db = FakeDB()

    result = asyncio.run(service.ensure_venues_exist(db, [_venue(42)]))

    assert result["success"] is True
    assert result["created"] == 1
    assert len(repository.rows) == 1
    assert db.savepoint_releases == 1
    assert db.rollback_calls == 0


def test_ensure_venues_exist_conflict_rereads_canonical_venue():
    service = VenueSyncService()
    repository = BulkVenueRepository(conflict_ids={"42"})
    service.repository = repository
    db = FakeDB()

    result = asyncio.run(service.ensure_venues_exist(db, [_venue(42)]))

    assert result["success"] is True
    assert result["created"] == 1
    assert db.savepoint_rollbacks == 1
    assert db.rollback_calls == 0
    assert list(repository.rows) == [("api-football", "42")]


def test_ensure_venues_exist_preserves_parent_work_after_conflict():
    service = VenueSyncService()
    repository = BulkVenueRepository(conflict_ids={"42"})
    service.repository = repository
    db = FakeDB()
    parent_work = ["match-write"]

    result = asyncio.run(service.ensure_venues_exist(db, [_venue(42)]))
    asyncio.run(db.flush())
    asyncio.run(db.commit())

    assert result["success"] is True
    assert parent_work == ["match-write"]
    assert db.rollback_calls == 0
    assert db.commit_calls == 1


def test_ensure_venues_exist_isolates_one_conflict_between_successful_venues():
    service = VenueSyncService()
    repository = BulkVenueRepository(conflict_ids={"42"})
    service.repository = repository
    db = FakeDB()

    result = asyncio.run(service.ensure_venues_exist(db, [_venue(41), _venue(42), _venue(43)]))

    assert result["success"] is True
    assert result["created"] == 3
    assert set(repository.rows) == {
        ("api-football", "41"),
        ("api-football", "42"),
        ("api-football", "43"),
    }
    assert db.savepoint_rollbacks == 1
    assert db.savepoint_releases == 2
    assert db.rollback_calls == 0


def test_ensure_venues_exist_preserves_unexpected_error_result():
    service = VenueSyncService()
    service.repository = BulkVenueRepository(error_ids={"42"})
    db = FakeDB()

    result = asyncio.run(service.ensure_venues_exist(db, [_venue(42)]))

    assert result["success"] is False
    assert result["errors"]
    assert db.rollback_calls == 0


def test_fixture_sync_invokes_venue_before_match_persistence():
    fixture = {
        "fixture": {
            "id": 7001,
            "date": "2026-06-15T18:00:00+00:00",
            "status": {"short": "NS", "elapsed": 0},
            "venue": {"id": 42, "name": "Arena", "city": "City"},
            "referee": "Referee Example",
        },
        "league": {"id": 39, "season": 2026},
        "teams": {"home": {"id": 1, "name": "Home"}, "away": {"id": 2, "name": "Away"}},
        "goals": {"home": 0, "away": 0},
    }
    service = FixtureSyncService(
        client=type("Client", (), {})(),
        team_service=FakeTeamService(),
        cache_service=FakeCacheService(),
        standing_service=FakeStandingService(),
    )
    venue_sync = FakeVenueSyncService()
    referee_sync = FakeRefereeSyncService()
    service.venue_sync_service = venue_sync
    service.referee_sync_service = referee_sync
    service.allowed_league_repository = FakeAllowedLeagueRepository()
    service.league_repository = FakeLeagueRepository()
    service.match_repository = FakeMatchRepository()
    db = FakeDB()

    result, _ = asyncio.run(service._process_sync_with_candidates(db, [fixture]))

    assert result["success"] is True
    assert referee_sync.names == ["Referee Example"]
    assert venue_sync.payloads == [fixture["fixture"]["venue"]]
    assert db.commit_calls == 0
