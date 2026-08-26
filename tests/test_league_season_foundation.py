from pathlib import Path
from datetime import datetime, timezone

import asyncio
import pytest

from app.models.league import League
from app.services.league_season_sync_service import LeagueSeasonSyncService
from app.services.league_sync_service import LeagueSyncService


def test_league_season_model_and_repository_exist():
    assert Path("app/models/league_season.py").exists()
    assert Path("app/repositories/league_season_repository.py").exists()


def test_league_season_migration_exists_and_marks_unique_identity():
    migration_files = list(Path("alembic/versions").glob("*league*season*.py"))
    assert migration_files, "League Season migration is missing"

    text = "\n".join(path.read_text(encoding="utf-8") for path in migration_files)
    assert "league_seasons" in text
    assert "league_id" in text
    assert "season" in text
    assert "UniqueConstraint" in text or "unique=True" in text or "uq_league_seasons_league_id_season" in text


class FakeSeasonRow:
    def __init__(self, **values):
        self.__dict__.update(values)


class FakeSeasonRepository:
    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.upserts = []

    async def list_by_league(self, db, league_id):
        return [row for row in self.rows if row.league_id == league_id]

    async def upsert_one(self, db, row):
        self.upserts.append(row)
        existing = next((item for item in self.rows if item.league_id == row["league_id"] and item.season == row["season"]), None)
        if existing is None:
            existing = FakeSeasonRow(id=len(self.rows) + 1, **row)
            self.rows.append(existing)
        else:
            for key, value in row.items():
                setattr(existing, key, value)
        return existing


def test_season_sync_maps_provider_fields_and_inserts():
    repository = FakeSeasonRepository()
    service = LeagueSeasonSyncService(repository)

    result = asyncio.run(service.sync_league_seasons(None, league_id=42, seasons=[
        {"year": 2026, "start": "2026-01-01", "end": "2026-12-31", "current": True},
    ]))

    assert result["inserted"] == 1
    assert result["updated"] == 0
    assert result["no_op"] == 0
    assert repository.upserts == [{
        "league_id": 42,
        "season": "2026",
        "provider": "api-football",
        "provider_id": None,
        "start_date": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "end_date": datetime(2026, 12, 31, tzinfo=timezone.utc),
        "current": True,
    }]


def test_season_sync_updates_changed_metadata_and_reconciles_bad_provider_id():
    repository = FakeSeasonRepository([FakeSeasonRow(
        id=1,
        league_id=42,
        season="2026",
        provider="api-football",
        provider_id="42",
        start_date=None,
        end_date=None,
        current=True,
    )])
    service = LeagueSeasonSyncService(repository)

    result = asyncio.run(service.sync_league_seasons(None, league_id=42, seasons=[
        {"year": "2026", "start": "2026-01-01", "end": "2026-12-31", "current": True},
    ]))

    assert result["updated"] == 1
    assert repository.rows[0].provider_id is None
    assert repository.rows[0].start_date == datetime(2026, 1, 1, tzinfo=timezone.utc)


def test_season_sync_does_not_write_matching_metadata():
    row = FakeSeasonRow(
        id=1,
        league_id=42,
        season="2026",
        provider="api-football",
        provider_id=None,
        start_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
        end_date=datetime(2026, 12, 31, tzinfo=timezone.utc),
        current=True,
    )
    repository = FakeSeasonRepository([row])

    result = asyncio.run(LeagueSeasonSyncService(repository).sync_league_seasons(
        None,
        league_id=42,
        seasons=[{"year": 2026, "start": "2026-01-01", "end": "2026-12-31", "current": True}],
    ))

    assert result["no_op"] == 1
    assert repository.upserts == []


def test_season_sync_rejects_multiple_current_seasons_without_writing():
    repository = FakeSeasonRepository()

    with pytest.raises(ValueError, match="Multiple current seasons"):
        asyncio.run(LeagueSeasonSyncService(repository).sync_league_seasons(
            None,
            league_id=42,
            seasons=[
                {"year": 2025, "current": True},
                {"year": 2026, "current": True},
            ],
        ))

    assert repository.upserts == []


def test_season_sync_rejects_invalid_season_without_writing():
    repository = FakeSeasonRepository()

    with pytest.raises(ValueError, match="Season year"):
        asyncio.run(LeagueSeasonSyncService(repository).sync_league_seasons(
            None,
            league_id=42,
            seasons=[{"year": None}],
        ))

    assert repository.upserts == []


class FakeLeagueRepository:
    def __init__(self, master):
        self.master = master

    async def find_by_provider_identity(self, db, provider, provider_id):
        if provider == "api-football" and str(provider_id) == self.master.provider_id:
            return self.master
        return None

    async def get_many_by_ids(self, db, league_ids):
        return [self.master] if self.master.league_id in league_ids else []


class FakeAllowedLeagueRepository:
    def __init__(self, allowed_ids):
        self.allowed_ids = set(allowed_ids)

    async def get_allowed_ids(self, db):
        return set(self.allowed_ids)


class FakeCache:
    def __init__(self):
        self.deleted = []

    def delete_sync(self, key):
        self.deleted.append(key)


def test_league_sync_resolves_master_and_syncs_only_allowed_seasons():
    master = League(
        league_id=42,
        provider="api-football",
        provider_id="7",
        name="Master League",
        is_featured=False,
        display_order=1,
    )
    season_repository = FakeSeasonRepository()
    cache = FakeCache()

    async def fetch_all_leagues():
        return {"response": [
            {"league": {"id": 7, "name": "Master League"}, "seasons": [{"year": 2026, "current": True}]},
            {"league": {"id": 8, "name": "Unresolved"}, "seasons": [{"year": 2026, "current": True}]},
        ]}

    service = LeagueSyncService(
        cache_service=cache,
        league_repository=FakeLeagueRepository(master),
        allowed_league_repository=FakeAllowedLeagueRepository({42}),
        fetch_all_leagues=fetch_all_leagues,
    )
    service.league_season_sync_service = LeagueSeasonSyncService(season_repository)

    result = asyncio.run(service.sync_all_leagues(None))

    assert result["season_sync"] == {"inserted": 1, "updated": 0, "no_op": 0, "leagues": 1}
    assert season_repository.upserts[0]["league_id"] == 42
    assert season_repository.upserts[0]["provider_id"] is None
