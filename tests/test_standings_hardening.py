import asyncio
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from app.models.standing import Standings
from app.repositories.standing_repository import StandingRepository
from app.services.scheduler import LiveUpdateScheduler
from app.services.standing_service import StandingService
from app.services import standing_sync_service as standing_sync_module
from app.services.standing_sync_service import (
    _STANDINGS_POST_COMMIT_CACHE_KEYS,
    _clear_standings_post_commit_cache_invalidation,
    _run_standings_post_commit_cache_invalidation,
)
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


class UpsertTrackingStandingRepository:
    def __init__(self):
        self.calls = []
        self.rows = []

    async def upsert_for_league_season(self, db, league_id, season, rows):
        self.calls.append((league_id, str(season)))
        self.rows.extend(rows)


class FakeStandingWriteDB:
    def __init__(self):
        self.flush_calls = 0

    async def flush(self):
        self.flush_calls += 1

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
    service.standing_repository = UpsertTrackingStandingRepository()
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
    assert len(service.standing_repository.rows) == 1
    assert service.standing_repository.rows[0]["team_id"] == 7
    assert service.standing_repository.calls == [(39, "2026")]


class ExecuteTrackingDB:
    def __init__(self):
        self.execute_calls = []

    async def execute(self, query):
        self.execute_calls.append(query)

        class FakeResult:
            def scalars(self):
                return self

            def all(self):
                return []

        return FakeResult()


class FakeSessionWithInfo:
    def __init__(self):
        self.info = {}


def test_standing_repository_upsert_uses_real_conflict_constraint():
    repository = StandingRepository()
    db = ExecuteTrackingDB()
    rows = [
        {
            "league_id": 39,
            "season": "2026",
            "team_id": 7,
            "position": 1,
            "team_name": "Seven",
            "team_logo": None,
            "group_name": None,
            "form": None,
            "description": None,
            "points": 20,
            "played": 8,
            "won": 6,
            "drawn": 2,
            "lost": 0,
            "goals_for": 15,
            "goals_against": 5,
            "goal_difference": 10,
        }
    ]

    asyncio.run(repository.upsert_for_league_season(db, 39, "2026", rows))

    sql_text = "\n".join(str(stmt) for stmt in db.execute_calls)
    assert "ON CONFLICT ON CONSTRAINT uq_standings_league_id_season_team_id" in sql_text
    assert "DO UPDATE SET" in sql_text


def test_standing_repository_upsert_cleans_stale_rows_by_team_scope():
    repository = StandingRepository()
    db = ExecuteTrackingDB()
    rows = [
        {
            "league_id": 39,
            "season": "2026",
            "team_id": 7,
            "position": 1,
            "team_name": "Seven",
            "team_logo": None,
            "group_name": None,
            "form": None,
            "description": None,
            "points": 20,
            "played": 8,
            "won": 6,
            "drawn": 2,
            "lost": 0,
            "goals_for": 15,
            "goals_against": 5,
            "goal_difference": 10,
        }
    ]

    asyncio.run(repository.upsert_for_league_season(db, 39, "2026", rows))

    sql_text = "\n".join(str(stmt) for stmt in db.execute_calls)
    assert "DELETE FROM standings" in sql_text
    assert "standings.team_id NOT IN" in sql_text


def test_standings_cache_invalidation_runs_only_after_commit(monkeypatch):
    deleted_keys = []

    def fake_cache_delete_sync(key):
        deleted_keys.append(key)

    monkeypatch.setattr(standing_sync_module, "cache_delete_sync", fake_cache_delete_sync)

    session = FakeSessionWithInfo()
    cache_key = "fover:standings:39:2026"
    session.info[_STANDINGS_POST_COMMIT_CACHE_KEYS] = {cache_key}

    _run_standings_post_commit_cache_invalidation(session)

    assert deleted_keys == [cache_key]
    assert _STANDINGS_POST_COMMIT_CACHE_KEYS not in session.info


def test_standings_cache_invalidation_cleared_on_rollback(monkeypatch):
    deleted_keys = []

    def fake_cache_delete_sync(key):
        deleted_keys.append(key)

    monkeypatch.setattr(standing_sync_module, "cache_delete_sync", fake_cache_delete_sync)

    session = FakeSessionWithInfo()
    session.info[_STANDINGS_POST_COMMIT_CACHE_KEYS] = {"fover:standings:39:2026"}

    _clear_standings_post_commit_cache_invalidation(session)
    _run_standings_post_commit_cache_invalidation(session)

    assert deleted_keys == []


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

    async def execute(self, query, params=None):
        class FakeResult:
            def __init__(self, value):
                self.value = value

            def scalar_one_or_none(self):
                return self.value

            def scalar_one(self):
                return self.value

        query_text = str(query)
        if "pg_try_advisory_lock" in query_text:
            return FakeResult(True)

        if "pg_advisory_unlock" in query_text:
            return FakeResult(True)

        return FakeResult(None)

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


def test_refresh_job_skips_when_advisory_lock_not_acquired(monkeypatch, caplog):
    scheduler = LiveUpdateScheduler()
    db = FakeSchedulerDB()

    async def fake_get_pairs(_db):
        raise AssertionError("Pairs query should not run when lock is unavailable")

    async def fake_acquire(_db):
        return False

    async def fake_release(_db):
        raise AssertionError("Lock release should not run when lock was never acquired")

    monkeypatch.setattr(scheduler, "_get_allowed_standings_pairs", fake_get_pairs)
    monkeypatch.setattr(scheduler, "_acquire_standings_refresh_lock", fake_acquire)
    monkeypatch.setattr(scheduler, "_release_standings_refresh_lock", fake_release)
    monkeypatch.setattr(scheduler_module, "async_session", lambda: FakeAsyncSessionContext(db))

    with caplog.at_level(logging.INFO):
        metrics = asyncio.run(scheduler._refresh_standings_job())

    assert metrics == {"processed_pairs": 0, "success_pairs": 0, "failed_pairs": 0}
    assert "STANDINGS_REFRESH_SKIPPED reason=lock_not_acquired" in caplog.text


def test_refresh_job_releases_advisory_lock_after_processing(monkeypatch):
    scheduler = LiveUpdateScheduler()
    db = FakeSchedulerDB()
    released = {"count": 0}

    async def fake_get_pairs(_db):
        return [(39, 2026)]

    async def fake_sync_standings(_db, league_id, season):
        return {"success": True, "updated": 1}

    async def fake_acquire(_db):
        return True

    async def fake_release(_db):
        released["count"] += 1

    monkeypatch.setattr(scheduler, "_get_allowed_standings_pairs", fake_get_pairs)
    monkeypatch.setattr(scheduler, "_acquire_standings_refresh_lock", fake_acquire)
    monkeypatch.setattr(scheduler, "_release_standings_refresh_lock", fake_release)
    monkeypatch.setattr(scheduler_module, "async_session", lambda: FakeAsyncSessionContext(db))
    monkeypatch.setattr(scheduler_module.football_service, "sync_standings", fake_sync_standings)

    metrics = asyncio.run(scheduler._refresh_standings_job())

    assert metrics == {"processed_pairs": 1, "success_pairs": 1, "failed_pairs": 0}
    assert released["count"] == 1


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


def test_scheduler_registers_refresh_events_job():
    scheduler = LiveUpdateScheduler()
    fake_engine = FakeSchedulerEngine()
    scheduler.scheduler = fake_engine

    scheduler.start()

    job_ids = [job["id"] for job in fake_engine.jobs]
    assert "refresh_events" in job_ids
    refresh_job = next(job for job in fake_engine.jobs if job["id"] == "refresh_events")
    assert refresh_job["name"] == "Refresh Active Match Events"
    assert refresh_job["max_instances"] == 1


class FakeEventRefreshCacheService:
    def __init__(self):
        self.delete_calls = []

    async def delete(self, key):
        self.delete_calls.append(key)


def test_event_refresh_uses_active_match_discovery_and_status_gate(monkeypatch, caplog):
    scheduler = LiveUpdateScheduler()
    db = FakeSchedulerDB()
    scheduler.cache_service = FakeEventRefreshCacheService()

    status_map = {501: "NS", 502: "1H", 503: "FT", 504: "LIVE"}
    synced = []
    removed = []

    async def fake_get_active_matches():
        return [501, 502, 503, 504]

    async def fake_get_status(_db, match_id):
        return status_map.get(match_id)

    async def fake_sync_events(_db, match_id):
        synced.append(match_id)
        return {"success": True, "count": 5}

    async def fake_remove_match_active(match_id):
        removed.append(match_id)

    monkeypatch.setattr(scheduler_module.active_match_service, "get_active_matches", fake_get_active_matches)
    monkeypatch.setattr(scheduler, "_get_match_status_for_event_refresh", fake_get_status)
    monkeypatch.setattr(scheduler_module.active_match_service, "remove_match_active", fake_remove_match_active)
    monkeypatch.setattr(scheduler_module, "async_session", lambda: FakeAsyncSessionContext(db))
    monkeypatch.setattr(scheduler_module.football_service, "sync_match_events", fake_sync_events)

    with caplog.at_level(logging.INFO):
        metrics = asyncio.run(scheduler._refresh_events_job())

    assert metrics == {
        "active_matches": 4,
        "processed_matches": 3,
        "synced_matches": 3,
        "skipped_matches": 1,
        "failed_matches": 0,
    }
    assert synced == [502, 503, 504]
    assert removed == []
    assert db.commit_calls == 3
    assert db.rollback_calls == 0
    assert scheduler.cache_service.delete_calls == [
        "fover:match:502:events",
        "fover:match:503:events",
        "fover:match:504:events",
    ]
    assert "EVENT_REFRESH_START" in caplog.text
    assert "EVENT_REFRESH_SKIPPED match_id=501 reason=status_blocked status=NS" in caplog.text
    assert "EVENT_REFRESH_SYNCED match_id=502" in caplog.text
    assert "EVENT_REFRESH_SYNCED match_id=503" in caplog.text
    assert "EVENT_REFRESH_SYNCED match_id=504" in caplog.text
    assert "EVENT_REFRESH_COMPLETE" in caplog.text


def test_event_refresh_allows_terminal_recovery_statuses(monkeypatch):
    scheduler = LiveUpdateScheduler()
    db = FakeSchedulerDB()
    scheduler.cache_service = FakeEventRefreshCacheService()
    synced = []

    async def fake_get_active_matches():
        return [701]

    async def fake_get_status(_db, _match_id):
        return "FT"

    async def fake_sync_events(_db, match_id):
        synced.append(match_id)
        return {"success": True, "count": 2}

    monkeypatch.setattr(scheduler_module.active_match_service, "get_active_matches", fake_get_active_matches)
    monkeypatch.setattr(scheduler, "_get_match_status_for_event_refresh", fake_get_status)
    monkeypatch.setattr(scheduler_module, "async_session", lambda: FakeAsyncSessionContext(db))
    monkeypatch.setattr(scheduler_module.football_service, "sync_match_events", fake_sync_events)

    metrics = asyncio.run(scheduler._refresh_events_job())

    assert metrics == {
        "active_matches": 1,
        "processed_matches": 1,
        "synced_matches": 1,
        "skipped_matches": 0,
        "failed_matches": 0,
    }
    assert synced == [701]
    assert db.commit_calls == 1
    assert db.rollback_calls == 0


def test_event_refresh_skips_before_refresh_interval(monkeypatch, caplog):
    scheduler = LiveUpdateScheduler()
    db = FakeSchedulerDB()
    scheduler.cache_service = FakeEventRefreshCacheService()

    async def fake_get_active_matches():
        return [901]

    async def fake_get_status(_db, _match_id):
        return "LIVE"

    async def fake_should_refresh(_db, _match_id):
        return False

    async def fake_sync_events(_db, _match_id):
        raise AssertionError("sync_match_events should not be called before refresh interval")

    monkeypatch.setattr(scheduler_module.active_match_service, "get_active_matches", fake_get_active_matches)
    monkeypatch.setattr(scheduler, "_get_match_status_for_event_refresh", fake_get_status)
    monkeypatch.setattr(scheduler, "_should_refresh_match_events", fake_should_refresh)
    monkeypatch.setattr(scheduler_module, "async_session", lambda: FakeAsyncSessionContext(db))
    monkeypatch.setattr(scheduler_module.football_service, "sync_match_events", fake_sync_events)

    with caplog.at_level(logging.INFO):
        metrics = asyncio.run(scheduler._refresh_events_job())

    assert metrics == {
        "active_matches": 1,
        "processed_matches": 1,
        "synced_matches": 0,
        "skipped_matches": 1,
        "failed_matches": 0,
    }
    assert "EVENT_REFRESH_SKIPPED match_id=901 reason=refresh_window" in caplog.text


def test_event_refresh_failure_isolation_and_metrics(monkeypatch, caplog):
    scheduler = LiveUpdateScheduler()
    db = FakeSchedulerDB()
    scheduler.cache_service = FakeEventRefreshCacheService()
    synced = []

    async def fake_get_active_matches():
        return [601, 602, 603]

    async def fake_get_status(_db, _match_id):
        return "1H"

    async def fake_sync_events(_db, match_id):
        if match_id == 602:
            raise RuntimeError("boom")
        if match_id == 603:
            return {"success": False, "message": "API error"}
        synced.append(match_id)
        return {"success": True, "count": 3}

    monkeypatch.setattr(scheduler_module.active_match_service, "get_active_matches", fake_get_active_matches)
    monkeypatch.setattr(scheduler, "_get_match_status_for_event_refresh", fake_get_status)
    monkeypatch.setattr(scheduler_module, "async_session", lambda: FakeAsyncSessionContext(db))
    monkeypatch.setattr(scheduler_module.football_service, "sync_match_events", fake_sync_events)

    with caplog.at_level(logging.INFO):
        metrics = asyncio.run(scheduler._refresh_events_job())

    assert metrics == {
        "active_matches": 3,
        "processed_matches": 3,
        "synced_matches": 1,
        "skipped_matches": 0,
        "failed_matches": 2,
    }
    assert synced == [601]
    assert db.commit_calls == 1
    assert db.rollback_calls == 2
    assert scheduler.cache_service.delete_calls == ["fover:match:601:events"]
    assert "EVENT_REFRESH_FAILED match_id=602" in caplog.text
    assert "EVENT_REFRESH_FAILED match_id=603 reason=API error" in caplog.text


def test_event_refresh_uses_active_matches_only(monkeypatch):
    scheduler = LiveUpdateScheduler()
    db = FakeSchedulerDB()
    scheduler.cache_service = FakeEventRefreshCacheService()
    called = {"active": 0, "status": 0}

    async def fake_get_active_matches():
        called["active"] += 1
        return []

    async def fake_get_status(_db, _match_id):
        called["status"] += 1
        return "1H"

    async def fake_sync_events(_db, _match_id):
        raise AssertionError("sync_match_events should not be called when no active matches")

    monkeypatch.setattr(scheduler_module.active_match_service, "get_active_matches", fake_get_active_matches)
    monkeypatch.setattr(scheduler, "_get_match_status_for_event_refresh", fake_get_status)
    monkeypatch.setattr(scheduler_module, "async_session", lambda: FakeAsyncSessionContext(db))
    monkeypatch.setattr(scheduler_module.football_service, "sync_match_events", fake_sync_events)

    metrics = asyncio.run(scheduler._refresh_events_job())

    assert metrics == {
        "active_matches": 0,
        "processed_matches": 0,
        "synced_matches": 0,
        "skipped_matches": 0,
        "failed_matches": 0,
    }
    assert called["active"] == 1
    assert called["status"] == 0


def test_event_refresh_suppresses_start_complete_info_logs_when_no_active_matches(monkeypatch, caplog):
    scheduler = LiveUpdateScheduler()
    db = FakeSchedulerDB()
    scheduler.cache_service = FakeEventRefreshCacheService()

    async def fake_get_active_matches():
        return []

    async def fake_get_status(_db, _match_id):
        raise AssertionError("status lookup should not run when no active matches")

    async def fake_sync_events(_db, _match_id):
        raise AssertionError("sync_match_events should not run when no active matches")

    monkeypatch.setattr(scheduler_module.active_match_service, "get_active_matches", fake_get_active_matches)
    monkeypatch.setattr(scheduler, "_get_match_status_for_event_refresh", fake_get_status)
    monkeypatch.setattr(scheduler_module, "async_session", lambda: FakeAsyncSessionContext(db))
    monkeypatch.setattr(scheduler_module.football_service, "sync_match_events", fake_sync_events)

    with caplog.at_level(logging.INFO):
        metrics = asyncio.run(scheduler._refresh_events_job())

    assert metrics["active_matches"] == 0
    assert "EVENT_REFRESH_START" not in caplog.text
    assert "EVENT_REFRESH_COMPLETE" not in caplog.text
    assert "EVENT_REFRESH_SYNCED" not in caplog.text


def test_scheduler_registers_refresh_statistics_job():
    scheduler = LiveUpdateScheduler()
    fake_engine = FakeSchedulerEngine()
    scheduler.scheduler = fake_engine

    scheduler.start()

    job_ids = [job["id"] for job in fake_engine.jobs]
    assert "refresh_statistics" in job_ids
    refresh_job = next(job for job in fake_engine.jobs if job["id"] == "refresh_statistics")
    assert refresh_job["name"] == "Refresh Active Match Statistics"
    assert refresh_job["max_instances"] == 1


def test_statistics_refresh_uses_active_match_discovery_and_status_gate(monkeypatch, caplog):
    scheduler = LiveUpdateScheduler()
    db = FakeSchedulerDB()
    scheduler.cache_service = FakeEventRefreshCacheService()

    status_map = {701: "NS", 702: "1H", 703: "FT", 704: "LIVE"}
    synced = []

    async def fake_get_active_matches():
        return [701, 702, 703, 704]

    async def fake_get_status(_db, match_id):
        return status_map.get(match_id)

    async def fake_sync_statistics(_db, match_id):
        synced.append(match_id)
        return {"success": True, "match_id": match_id}

    monkeypatch.setattr(scheduler_module.active_match_service, "get_active_matches", fake_get_active_matches)
    monkeypatch.setattr(scheduler, "_get_match_status_for_event_refresh", fake_get_status)
    monkeypatch.setattr(scheduler_module, "async_session", lambda: FakeAsyncSessionContext(db))
    monkeypatch.setattr(scheduler_module.football_service, "sync_match_statistics", fake_sync_statistics)

    with caplog.at_level(logging.INFO):
        metrics = asyncio.run(scheduler._refresh_statistics_job())

    assert metrics == {
        "active_matches": 4,
        "processed_matches": 2,
        "synced_matches": 2,
        "skipped_matches": 2,
        "failed_matches": 0,
    }
    assert synced == [702, 704]
    assert db.commit_calls == 2
    assert db.rollback_calls == 0
    assert scheduler.cache_service.delete_calls == [
        "fover:match:702:statistics",
        "fover:match:704:statistics",
    ]
    assert "STATISTICS_REFRESH_START" in caplog.text
    assert "STATISTICS_REFRESH_SKIPPED match_id=701 reason=status_blocked status=NS" in caplog.text
    assert "STATISTICS_REFRESH_SKIPPED match_id=703 reason=status_blocked status=FT" in caplog.text
    assert "STATISTICS_REFRESH_SYNCED match_id=702" in caplog.text
    assert "STATISTICS_REFRESH_SYNCED match_id=704" in caplog.text
    assert "STATISTICS_REFRESH_COMPLETE" in caplog.text


def test_statistics_refresh_failure_isolation_and_metrics(monkeypatch, caplog):
    scheduler = LiveUpdateScheduler()
    db = FakeSchedulerDB()
    scheduler.cache_service = FakeEventRefreshCacheService()
    synced = []

    async def fake_get_active_matches():
        return [801, 802, 803]

    async def fake_get_status(_db, _match_id):
        return "1H"

    async def fake_sync_statistics(_db, match_id):
        if match_id == 802:
            raise RuntimeError("boom")
        if match_id == 803:
            return {"success": False, "message": "Statistics not found"}
        synced.append(match_id)
        return {"success": True, "match_id": match_id}

    monkeypatch.setattr(scheduler_module.active_match_service, "get_active_matches", fake_get_active_matches)
    monkeypatch.setattr(scheduler, "_get_match_status_for_event_refresh", fake_get_status)
    monkeypatch.setattr(scheduler_module, "async_session", lambda: FakeAsyncSessionContext(db))
    monkeypatch.setattr(scheduler_module.football_service, "sync_match_statistics", fake_sync_statistics)

    with caplog.at_level(logging.INFO):
        metrics = asyncio.run(scheduler._refresh_statistics_job())

    assert metrics == {
        "active_matches": 3,
        "processed_matches": 3,
        "synced_matches": 1,
        "skipped_matches": 0,
        "failed_matches": 2,
    }
    assert synced == [801]
    assert db.commit_calls == 1
    assert db.rollback_calls == 2
    assert scheduler.cache_service.delete_calls == ["fover:match:801:statistics"]
    assert "STATISTICS_REFRESH_FAILED match_id=802" in caplog.text
    assert "STATISTICS_REFRESH_FAILED match_id=803 reason=Statistics not found" in caplog.text


def test_statistics_refresh_uses_active_matches_only(monkeypatch):
    scheduler = LiveUpdateScheduler()
    db = FakeSchedulerDB()
    scheduler.cache_service = FakeEventRefreshCacheService()
    called = {"active": 0, "status": 0}

    async def fake_get_active_matches():
        called["active"] += 1
        return []

    async def fake_get_status(_db, _match_id):
        called["status"] += 1
        return "1H"

    async def fake_sync_statistics(_db, _match_id):
        raise AssertionError("sync_match_statistics should not be called when no active matches")

    monkeypatch.setattr(scheduler_module.active_match_service, "get_active_matches", fake_get_active_matches)
    monkeypatch.setattr(scheduler, "_get_match_status_for_event_refresh", fake_get_status)
    monkeypatch.setattr(scheduler_module, "async_session", lambda: FakeAsyncSessionContext(db))
    monkeypatch.setattr(scheduler_module.football_service, "sync_match_statistics", fake_sync_statistics)

    metrics = asyncio.run(scheduler._refresh_statistics_job())

    assert metrics == {
        "active_matches": 0,
        "processed_matches": 0,
        "synced_matches": 0,
        "skipped_matches": 0,
        "failed_matches": 0,
    }
    assert called["active"] == 1
    assert called["status"] == 0


def test_lineup_refresh_state_migration_exists():
    migration_path = Path("alembic/versions/f4a5b6c7d8e9_create_lineup_refresh_state_table.py")
    migration_text = migration_path.read_text(encoding="utf-8")

    assert "lineup_refresh_state" in migration_text
    assert "last_refreshed_at" in migration_text
    assert "updated_at" in migration_text