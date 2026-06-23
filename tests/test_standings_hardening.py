import asyncio
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from app.models.standing import Standings
from app.services.scheduler import LiveUpdateScheduler
from app.services.standing_service import StandingService
import app.services.scheduler as scheduler_module


class ExplodingStandingClient:
    def __init__(self):
        self.calls = 0

    async def get(self, path, params=None):
        self.calls += 1
        raise AssertionError("API-Football should not be called from user standings reads")


class FakeTeamService:
    def __init__(self):
        self.calls = []

    async def ensure_teams_exist(self, db, teams):
        self.calls.append(list(teams))
        return None


class RecordingCacheService:
    def __init__(self, cached=None):
        self.cached = cached
        self.set_calls = []
        self.deleted = []

    async def get_json(self, key):
        return self.cached

    async def set_json(self, key, payload, ttl):
        self.set_calls.append((key, payload, ttl))

    def delete_sync(self, key):
        self.deleted.append(key)

    async def delete(self, key):
        self.deleted.append(key)


class StaticStandingRepository:
    def __init__(self, rows):
        self.rows = list(rows)
        self.calls = []

    async def get_for_league_season(self, db, league_id, season):
        self.calls.append((league_id, str(season)))
        return list(self.rows)


class DeleteTrackingStandingRepository:
    def __init__(self):
        self.deleted_pairs = []

    async def delete_for_league_season(self, db, league_id, season):
        self.deleted_pairs.append((league_id, str(season)))


class FakeStandingWriteDB:
    def __init__(self):
        self.added = []
        self.flush_calls = 0

    async def flush(self):
        self.flush_calls += 1

    def add(self, instance):
        self.added.append(instance)


def make_standing_row(team_id=1, position=1):
    timestamp = datetime(2026, 6, 23, tzinfo=timezone.utc)
    return SimpleNamespace(
        id=position,
        league_id=39,
        season="2026",
        team_id=team_id,
        team_name=f"Team {team_id}",
        team_logo=None,
        group_name=None,
        form=None,
        description=None,
        position=position,
        points=10,
        played=5,
        won=3,
        drawn=1,
        lost=1,
        goals_for=8,
        goals_against=3,
        goal_difference=5,
        created_at=timestamp,
        updated_at=timestamp,
    )


def test_get_cached_standings_redis_miss_reads_postgres_and_populates_cache():
    client = ExplodingStandingClient()
    cache_service = RecordingCacheService(cached=None)
    service = StandingService(client=client, team_service=FakeTeamService(), cache_service=cache_service)
    service.standing_repository = StaticStandingRepository([make_standing_row(team_id=7, position=1)])

    result = asyncio.run(service.get_cached_standings(object(), 39, 2026))

    assert result is not None
    assert result[0]["team_id"] == 7
    assert client.calls == 0
    assert service.standing_repository.calls == [(39, "2026")]
    assert len(cache_service.set_calls) == 1


def test_get_cached_standings_postgres_miss_returns_none_without_api_call():
    client = ExplodingStandingClient()
    cache_service = RecordingCacheService(cached=None)
    service = StandingService(client=client, team_service=FakeTeamService(), cache_service=cache_service)
    service.standing_repository = StaticStandingRepository([])

    result = asyncio.run(service.get_cached_standings(object(), 39, 2026))

    assert result is None
    assert client.calls == 0
    assert service.standing_repository.calls == [(39, "2026")]
    assert cache_service.set_calls == []


def test_upsert_standings_deduplicates_duplicate_team_rows():
    service = StandingService(
        client=ExplodingStandingClient(),
        team_service=FakeTeamService(),
        cache_service=RecordingCacheService(),
    )
    service.standing_repository = DeleteTrackingStandingRepository()
    db = FakeStandingWriteDB()
    standings_data = [
        {
            "rank": 1,
            "team": {"id": 7, "name": "Seven", "logo": None, "country": "MM"},
            "points": 20,
            "all": {"played": 8, "win": 6, "draw": 2, "lose": 0},
            "goalsDiff": 10,
        },
        {
            "rank": 2,
            "team": {"id": 7, "name": "Seven", "logo": None, "country": "MM"},
            "points": 18,
            "all": {"played": 8, "win": 5, "draw": 3, "lose": 0},
            "goalsDiff": 8,
        },
    ]

    inserted = asyncio.run(service.upsert_standings(db, standings_data, 39, "2026"))

    assert inserted == 1
    assert len(db.added) == 1
    assert db.added[0].team_id == 7
    assert service.standing_repository.deleted_pairs == [(39, "2026")]


class QueryInspectResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)


class QueryInspectDB:
    def __init__(self, rows):
        self.rows = rows
        self.statement_text = None

    async def execute(self, query):
        self.statement_text = str(query)
        return QueryInspectResult(self.rows)


def test_refresh_pair_query_filters_allowed_leagues_only():
    scheduler = LiveUpdateScheduler()
    db = QueryInspectDB([(39, 2026), (140, 2025)])

    pairs = asyncio.run(scheduler._get_allowed_standings_pairs(db))

    assert pairs == [(39, 2026), (140, 2025)]
    assert "JOIN allowed_leagues" in db.statement_text


def test_refresh_pair_query_uses_match_season_and_distinct_pairs():
    scheduler = LiveUpdateScheduler()
    db = QueryInspectDB([(39, 2026)])

    asyncio.run(scheduler._get_allowed_standings_pairs(db))

    assert "DISTINCT" in db.statement_text.upper()
    assert "matches.season" in db.statement_text
    assert "leagues.season" not in db.statement_text
    assert "matches.season IS NOT NULL" in db.statement_text


class FakeSchedulerDB:
    def __init__(self):
        self.commit_calls = 0
        self.rollback_calls = 0

    async def commit(self):
        self.commit_calls += 1

    async def rollback(self):
        self.rollback_calls += 1


class FakeAsyncSessionContext:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, exc_type, exc, tb):
        return False


def test_refresh_job_isolates_failures_and_continues(monkeypatch, caplog):
    scheduler = LiveUpdateScheduler()
    db = FakeSchedulerDB()
    pairs = [(39, 2026), (140, 2025), (39, 2025)]
    sync_calls = []

    async def fake_get_pairs(_db):
        return list(pairs)

    async def fake_sync_standings(_db, league_id, season):
        sync_calls.append((league_id, season))
        if (league_id, season) == (140, 2025):
            raise RuntimeError("boom")
        return {"success": True, "updated": 1}

    monkeypatch.setattr(scheduler, "_get_allowed_standings_pairs", fake_get_pairs)
    monkeypatch.setattr(scheduler_module, "async_session", lambda: FakeAsyncSessionContext(db))
    monkeypatch.setattr(scheduler_module.football_service, "sync_standings", fake_sync_standings)

    with caplog.at_level(logging.INFO):
        metrics = asyncio.run(scheduler._refresh_standings_job())

    assert metrics == {"processed_pairs": 3, "success_pairs": 2, "failed_pairs": 1}
    assert sync_calls == pairs
    assert db.commit_calls == 2
    assert db.rollback_calls == 1
    assert "STANDINGS_REFRESH_START" in caplog.text
    assert "STANDINGS_REFRESH_LEAGUE" in caplog.text
    assert "STANDINGS_REFRESH_SUCCESS" in caplog.text
    assert "STANDINGS_REFRESH_FAILED" in caplog.text
    assert "STANDINGS_REFRESH_COMPLETE" in caplog.text


def test_standings_model_declares_index_and_unique_constraint():
    index_names = {index.name for index in Standings.__table__.indexes}
    constraint_names = {constraint.name for constraint in Standings.__table__.constraints}

    assert "ix_standings_league_id_season_position" in index_names
    assert "uq_standings_league_id_season_team_id" in constraint_names


def test_standings_migration_contains_index_and_unique_constraint():
    migration_path = Path("alembic/versions/e4f5a6b7c8d9_add_standings_constraints.py")
    migration_text = migration_path.read_text(encoding="utf-8")

    assert "op.create_index(" in migration_text
    assert "ix_standings_league_id_season_position" in migration_text
    assert "op.create_unique_constraint(" in migration_text
    assert "uq_standings_league_id_season_team_id" in migration_text
    assert "ROW_NUMBER() OVER" in migration_text


def test_lineup_refresh_candidate_query_filters_allowed_leagues_status_and_season():
    scheduler = LiveUpdateScheduler()
    db = QueryInspectDB([(1539017,), (1539018,)])

    candidates = asyncio.run(
        scheduler._get_lineup_refresh_candidates(
            db,
            now_utc=datetime(2026, 6, 23, 10, 0, tzinfo=timezone.utc),
            window_minutes=90,
        )
    )

    assert candidates == [1539017, 1539018]
    assert "JOIN allowed_leagues" in db.statement_text
    assert "matches.season IS NOT NULL" in db.statement_text
    assert "matches.status =" in db.statement_text


def test_lineup_refresh_candidate_query_applies_90_minute_window():
    scheduler = LiveUpdateScheduler()
    db = QueryInspectDB([(1539017,)])

    asyncio.run(
        scheduler._get_lineup_refresh_candidates(
            db,
            now_utc=datetime(2026, 6, 23, 10, 0, tzinfo=timezone.utc),
            window_minutes=90,
        )
    )

    sql = db.statement_text
    assert "matches.match_time >" in sql
    assert "matches.match_time <=" in sql
    assert "ORDER BY matches.match_time ASC" in sql


class FakeLineupRefreshStateRepository:
    def __init__(self, cooldown_matches=None):
        self.cooldown_matches = set(cooldown_matches or [])
        self.touch_calls = []
        self.cooldown_checks = []

    async def is_on_cooldown(self, db, match_id, cooldown_seconds, now_utc=None):
        self.cooldown_checks.append((match_id, cooldown_seconds))
        return match_id in self.cooldown_matches

    async def touch(self, db, match_id, refreshed_at=None):
        self.touch_calls.append((match_id, refreshed_at))


def test_lineup_refresh_job_respects_cooldown(monkeypatch, caplog):
    scheduler = LiveUpdateScheduler()
    db = FakeSchedulerDB()
    sync_calls = []

    async def fake_candidates(_db, now_utc=None, window_minutes=90):
        return [101, 102]

    async def fake_sync_lineup(_db, match_id):
        sync_calls.append(match_id)
        return {"success": True, "match_id": match_id, "created": True, "updated": False}

    scheduler.lineup_refresh_state_repository = FakeLineupRefreshStateRepository(cooldown_matches={101})
    monkeypatch.setattr(scheduler, "_get_lineup_refresh_candidates", fake_candidates)
    monkeypatch.setattr(scheduler_module, "async_session", lambda: FakeAsyncSessionContext(db))
    monkeypatch.setattr(scheduler_module.football_service, "sync_match_lineup", fake_sync_lineup)

    with caplog.at_level(logging.INFO):
        metrics = asyncio.run(scheduler._refresh_lineups_job())

    assert metrics == {
        "candidate_matches": 2,
        "processed_matches": 2,
        "synced_matches": 1,
        "skipped_matches": 1,
        "failed_matches": 0,
    }
    assert sync_calls == [102]
    assert db.commit_calls == 1
    assert db.rollback_calls == 0
    assert "LINEUP_REFRESH_SKIPPED match_id=101 reason=cooldown" in caplog.text
    assert "LINEUP_REFRESH_SYNCED match_id=102" in caplog.text


def test_lineup_refresh_job_isolates_failures_and_continues(monkeypatch, caplog):
    scheduler = LiveUpdateScheduler()
    db = FakeSchedulerDB()
    sync_calls = []

    async def fake_candidates(_db, now_utc=None, window_minutes=90):
        return [201, 202, 203]

    async def fake_sync_lineup(_db, match_id):
        sync_calls.append(match_id)
        if match_id == 202:
            raise RuntimeError("boom")
        if match_id == 203:
            return {"success": False, "match_id": 203, "reason": "lineup_sync_failed"}
        return {"success": True, "match_id": 201, "created": True, "updated": False}

    scheduler.lineup_refresh_state_repository = FakeLineupRefreshStateRepository()
    monkeypatch.setattr(scheduler, "_get_lineup_refresh_candidates", fake_candidates)
    monkeypatch.setattr(scheduler_module, "async_session", lambda: FakeAsyncSessionContext(db))
    monkeypatch.setattr(scheduler_module.football_service, "sync_match_lineup", fake_sync_lineup)

    with caplog.at_level(logging.INFO):
        metrics = asyncio.run(scheduler._refresh_lineups_job())

    assert metrics == {
        "candidate_matches": 3,
        "processed_matches": 3,
        "synced_matches": 1,
        "skipped_matches": 0,
        "failed_matches": 2,
    }
    assert sync_calls == [201, 202, 203]
    assert db.commit_calls == 1
    assert db.rollback_calls == 2
    assert "LINEUP_REFRESH_FAILED match_id=202" in caplog.text
    assert "LINEUP_REFRESH_FAILED match_id=203 reason=lineup_sync_failed" in caplog.text
    assert "LINEUP_REFRESH_COMPLETE" in caplog.text


class FakeSchedulerEngine:
    def __init__(self):
        self.jobs = []
        self.started = False
        self.stopped = False

    def add_job(self, func, trigger, id, name, max_instances):
        self.jobs.append({"func": func, "trigger": trigger, "id": id, "name": name, "max_instances": max_instances})

    def start(self):
        self.started = True

    def shutdown(self, wait=True):
        self.stopped = True


def test_scheduler_registers_refresh_lineups_job():
    scheduler = LiveUpdateScheduler()
    fake_engine = FakeSchedulerEngine()
    scheduler.scheduler = fake_engine

    scheduler.start()

    job_ids = [job["id"] for job in fake_engine.jobs]
    assert "refresh_lineups" in job_ids
    refresh_job = next(job for job in fake_engine.jobs if job["id"] == "refresh_lineups")
    assert refresh_job["name"] == "Refresh Lineups"
    assert refresh_job["max_instances"] == 1
    assert fake_engine.started is True


def test_lineup_refresh_state_migration_exists():
    migration_path = Path("alembic/versions/f4a5b6c7d8e9_create_lineup_refresh_state_table.py")
    migration_text = migration_path.read_text(encoding="utf-8")

    assert "lineup_refresh_state" in migration_text
    assert "last_refreshed_at" in migration_text
    assert "updated_at" in migration_text