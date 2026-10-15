import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import app.api.matches as matches_api
import app.services.scheduler as scheduler_module
from app.core.security import get_current_active_admin
from app.db import get_db
from app.main import app
from app.services.scheduler import LiveUpdateScheduler
from app.services.odds_sync_service import (
    CANDIDATE_READY,
    COMMIT_UNKNOWN,
    IDENTITY_REJECTED,
    IDENTITY_UNAVAILABLE,
    NO_DATA,
    OddsIdentityLookupError,
    OddsMatchIdentityError,
    PROVIDER_FAILED,
    RETRY_BLOCKED,
)
from app.services.resource_lock import ResourceLockAcquisitionError
from fastapi import HTTPException
from fastapi.testclient import TestClient


class Result:
    def __init__(self, rows=None, first_row=None):
        self.rows = rows or []
        self.first_row = first_row

    def all(self):
        return self.rows

    def first(self):
        return self.first_row


class DB:
    def __init__(self):
        self.commit_calls = 0
        self.rollback_calls = 0
        self.queries = 0
        self.operations = []

    async def execute(self, _statement):
        self.queries += 1
        if self.queries == 1:
            return Result(rows=[(100, 200, "NS", datetime.now(timezone.utc))])
        return Result(first_row=None)

    async def commit(self):
        self.commit_calls += 1
        self.operations.append("commit")

    async def rollback(self):
        self.rollback_calls += 1
        self.operations.append("rollback")


class Session:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, exc_type, exc, tb):
        return False


class Cache:
    def __init__(self, fail=False):
        self.fail = fail
        self.deleted = []

    async def delete(self, key):
        if self.fail:
            raise RuntimeError("cache unavailable")
        self.deleted.append(key)
        return True


def _candidate_result():
    return {
        "status": CANDIDATE_READY,
        "success": False,
        "reason": "VALIDATED",
        "analytics": {"success": True, "accepted_count": 3},
        "_transaction_context": {
            "local_match_id": 100,
            "provider_fixture_id": 200,
            "persistence_rows": [{"last_updated": datetime.now(timezone.utc)}],
        },
    }


def _allow_odds_retry(monkeypatch):
    async def eligible(*_args, **_kwargs):
        return {
            "eligible": True,
            "status": "ELIGIBLE",
            "cache_invalidation_pending": False,
            "retry_due": False,
        }

    async def no_op(*_args, **_kwargs):
        return None

    service = scheduler_module.football_service.odds_sync_service
    monkeypatch.setattr(service, "check_retry_eligibility", eligible)
    monkeypatch.setattr(service.retry_state_repository, "mark_success_pending_cache", no_op)
    monkeypatch.setattr(service.retry_state_repository, "clear_cache_invalidation_pending", no_op)
    async def no_pending(*_args, **_kwargs):
        return []

    monkeypatch.setattr(service.retry_state_repository, "get_pending_cache_invalidations", no_pending)
    monkeypatch.setattr(service.retry_state_repository, "get_pending_reconciliations", no_pending)

    async def prepare_attempt(*_args, **_kwargs):
        return "attempt-1"

    async def resolve_attempt(*_args, **_kwargs):
        return None

    monkeypatch.setattr(service, "prepare_refresh_attempt", prepare_attempt)
    monkeypatch.setattr(service, "resolve_attempt_without_candidate", resolve_attempt)


def test_odds_scheduler_uses_fixture_lock_before_provider(monkeypatch):
    scheduler = LiveUpdateScheduler()
    db = DB()
    cache = Cache()
    calls = []

    async def fake_refresh(*args, **_kwargs):
        calls.append("provider")
        return _candidate_result()

    async def fake_lock(_db, resource_type, resource_identity, operation):
        calls.append((resource_type, resource_identity))
        return True, await operation()

    scheduler.cache_service = cache
    _allow_odds_retry(monkeypatch)
    monkeypatch.setattr(scheduler_module, "async_session", lambda: Session(db))
    monkeypatch.setattr(scheduler_module, "run_with_resource_lock", fake_lock)
    monkeypatch.setattr(scheduler_module.football_service.odds_sync_service, "refresh_odds", fake_refresh)
    monkeypatch.setattr(
        scheduler_module.football_service.odds_sync_service.odds_repository,
        "get_fixture_odds",
        lambda *_args: asyncio.sleep(0, result=[]),
    )

    result = asyncio.run(scheduler._refresh_odds_job())

    assert result["refreshed_matches"] == 1
    assert calls == [("odds", 100), "provider", ("odds", 100)]
    assert db.commit_calls == 3
    assert cache.deleted == ["fover:match:100:odds"]


def test_odds_scheduler_does_not_call_provider_when_lock_conflicts(monkeypatch):
    scheduler = LiveUpdateScheduler()
    db = DB()
    provider_calls = []

    async def fake_refresh(*args, **_kwargs):
        provider_calls.append(True)
        return _candidate_result()

    async def unavailable_lock(*_args):
        return False, None

    monkeypatch.setattr(scheduler_module, "async_session", lambda: Session(db))
    _allow_odds_retry(monkeypatch)
    monkeypatch.setattr(scheduler_module, "run_with_resource_lock", unavailable_lock)
    monkeypatch.setattr(scheduler_module.football_service.odds_sync_service, "refresh_odds", fake_refresh)

    result = asyncio.run(scheduler._refresh_odds_job())

    assert result["refreshed_matches"] == 0
    assert result["skipped_matches"] == 1
    assert provider_calls == []
    assert db.commit_calls == 0


def test_odds_scheduler_lock_acquisition_error_is_failure_not_contention(monkeypatch):
    scheduler = LiveUpdateScheduler()
    db = DB()
    provider_calls = []

    async def fail_lock(*_args):
        raise ResourceLockAcquisitionError("fover:sync:odds:100") from RuntimeError(
            "database unavailable"
        )

    async def fake_refresh(*_args, **_kwargs):
        provider_calls.append(True)
        return _candidate_result()

    _allow_odds_retry(monkeypatch)
    monkeypatch.setattr(scheduler_module, "async_session", lambda: Session(db))
    monkeypatch.setattr(scheduler_module, "run_with_resource_lock", fail_lock)
    monkeypatch.setattr(
        scheduler_module.football_service.odds_sync_service,
        "refresh_odds",
        fake_refresh,
    )

    result = asyncio.run(scheduler._refresh_odds_job())

    assert result["failed_matches"] == 1
    assert result["skipped_matches"] == 0
    assert result["refreshed_matches"] == 0
    assert provider_calls == []
    assert db.rollback_calls >= 1


def test_odds_cache_failure_does_not_rollback_after_commit(monkeypatch):
    scheduler = LiveUpdateScheduler()
    db = DB()
    cache = Cache(fail=True)

    async def fake_refresh(*args, **_kwargs):
        return _candidate_result()

    async def fake_lock(_db, resource_type, resource_identity, operation):
        assert (resource_type, resource_identity) == ("odds", 100)
        return True, await operation()

    scheduler.cache_service = cache
    _allow_odds_retry(monkeypatch)
    monkeypatch.setattr(scheduler_module, "async_session", lambda: Session(db))
    monkeypatch.setattr(scheduler_module, "run_with_resource_lock", fake_lock)
    monkeypatch.setattr(scheduler_module.football_service.odds_sync_service, "refresh_odds", fake_refresh)
    monkeypatch.setattr(
        scheduler_module.football_service.odds_sync_service.odds_repository,
        "get_fixture_odds",
        lambda *_args: asyncio.sleep(0, result=[]),
    )

    result = asyncio.run(scheduler._refresh_odds_job())

    assert result["refreshed_matches"] == 1
    assert db.commit_calls == 2
    assert db.operations[-1] == "commit"


def test_manual_odds_route_uses_local_match_lock_and_invalidates_after_commit(monkeypatch):
    db = DB()
    events = []

    async def allowed_match(_match_id, _db):
        return type("MatchRef", (), {"provider_fixture_id": 200})()

    async def fake_refresh(*_args, **_kwargs):
        events.append("sync")
        return _candidate_result()

    async def fake_lock(_db, resource_type, resource_identity, operation):
        events.append((resource_type, resource_identity))
        return True, await operation()

    class Cache:
        async def delete(self, key):
            events.append(("cache", key))
            return True

    async def commit():
        events.append("commit")
        db.commit_calls += 1

    _allow_odds_retry(monkeypatch)
    db.commit = commit
    monkeypatch.setattr(matches_api, "_assert_match_allowed", allowed_match)
    monkeypatch.setattr(matches_api, "async_session", lambda: Session(db))
    monkeypatch.setattr(matches_api, "run_with_resource_lock", fake_lock)
    monkeypatch.setattr(matches_api.football_service.odds_sync_service, "refresh_odds", fake_refresh)
    monkeypatch.setattr(matches_api, "CacheService", Cache)

    result = asyncio.run(matches_api.sync_match_odds_route(100, db))

    assert result["success"] is True
    assert events == [
        ("odds", "100"),
        "commit",
        "sync",
        "commit",
        ("cache", "fover:match:100:odds"),
        ("odds", "100"),
        "commit",
    ]


def test_manual_odds_route_returns_409_on_genuine_lock_contention(monkeypatch):
    db = DB()
    provider_calls = []

    async def allowed_match(_match_id, _db):
        return type("MatchRef", (), {"provider_fixture_id": 200})()

    async def contended_lock(*_args):
        return False, None

    async def fail_if_called(*_args, **_kwargs):
        provider_calls.append(True)
        raise AssertionError("sync callback must not run when lock is contended")

    async def override_db():
        yield db

    async def override_admin():
        return SimpleNamespace(role="admin", is_active=True)

    old_db_override = app.dependency_overrides.get(get_db)
    old_admin_override = app.dependency_overrides.get(get_current_active_admin)
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_active_admin] = override_admin
    monkeypatch.setattr(matches_api, "_assert_match_allowed", allowed_match)
    monkeypatch.setattr(matches_api, "run_with_resource_lock", contended_lock)
    monkeypatch.setattr(
        matches_api.football_service.odds_sync_service,
        "refresh_odds",
        fail_if_called,
    )
    try:
        response = TestClient(app).post("/api/matches/sync/100/odds")
    finally:
        if old_db_override is None:
            app.dependency_overrides.pop(get_db, None)
        else:
            app.dependency_overrides[get_db] = old_db_override
        if old_admin_override is None:
            app.dependency_overrides.pop(get_current_active_admin, None)
        else:
            app.dependency_overrides[get_current_active_admin] = old_admin_override

    assert response.status_code == 409
    assert provider_calls == []


def test_manual_odds_route_maps_acquisition_error_to_safe_503_and_rolls_back(monkeypatch):
    db = DB()
    provider_calls = []
    internal_error = "database connection details must not be exposed"

    async def allowed_match(_match_id, _db):
        return type("MatchRef", (), {"provider_fixture_id": 200})()

    async def fail_lock(*_args):
        raise ResourceLockAcquisitionError("fover:sync:odds:100") from RuntimeError(
            internal_error
        )

    async def fail_if_called(*_args, **_kwargs):
        provider_calls.append(True)
        raise AssertionError("sync callback must not run when lock acquisition fails")

    async def override_db():
        yield db

    async def override_admin():
        return SimpleNamespace(role="admin", is_active=True)

    old_db_override = app.dependency_overrides.get(get_db)
    old_admin_override = app.dependency_overrides.get(get_current_active_admin)
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_active_admin] = override_admin
    monkeypatch.setattr(matches_api, "_assert_match_allowed", allowed_match)
    monkeypatch.setattr(matches_api, "run_with_resource_lock", fail_lock)
    monkeypatch.setattr(
        matches_api.football_service.odds_sync_service,
        "refresh_odds",
        fail_if_called,
    )
    try:
        response = TestClient(app).post("/api/matches/sync/100/odds")
    finally:
        if old_db_override is None:
            app.dependency_overrides.pop(get_db, None)
        else:
            app.dependency_overrides[get_db] = old_db_override
        if old_admin_override is None:
            app.dependency_overrides.pop(get_current_active_admin, None)
        else:
            app.dependency_overrides[get_current_active_admin] = old_admin_override

    assert response.status_code == 503
    assert response.json() == {"detail": "Resource lock unavailable"}
    assert internal_error not in response.text
    assert db.rollback_calls == 1
    assert provider_calls == []


def test_manual_odds_route_rolls_back_failed_sync_without_cache_invalidation(monkeypatch):
    db = DB()
    cache_calls = []

    async def allowed_match(_match_id, _db):
        return type("MatchRef", (), {"provider_fixture_id": 200})()

    async def fake_refresh(*_args, **_kwargs):
        return {"status": NO_DATA, "success": False, "reason": "EMPTY_PROVIDER_RESPONSE"}

    async def fake_lock(_db, _resource_type, _resource_identity, operation):
        return True, await operation()

    class Cache:
        async def delete(self, key):
            cache_calls.append(key)
            return True

    _allow_odds_retry(monkeypatch)
    async def no_failure(*_args, **_kwargs):
        return None

    monkeypatch.setattr(
        matches_api.football_service.odds_sync_service.retry_state_repository,
        "record_failure",
        no_failure,
    )
    monkeypatch.setattr(matches_api, "_assert_match_allowed", allowed_match)
    monkeypatch.setattr(matches_api, "async_session", lambda: Session(db))
    monkeypatch.setattr(matches_api, "run_with_resource_lock", fake_lock)
    monkeypatch.setattr(matches_api.football_service.odds_sync_service, "refresh_odds", fake_refresh)
    monkeypatch.setattr(matches_api, "CacheService", Cache)

    try:
        asyncio.run(matches_api.sync_match_odds_route(100, db))
    except HTTPException as exc:
        assert exc.status_code == 502
    else:
        raise AssertionError("expected failed Odds sync")

    assert db.rollback_calls == 1
    assert cache_calls == []


def test_manual_odds_identity_rejection_returns_422_without_failure_record(monkeypatch):
    db = DB()
    provider_calls = []
    begin_attempt = AsyncMock()
    record_failure = AsyncMock()

    async def allowed_match(_match_id, _db):
        return type("MatchRef", (), {"provider_fixture_id": 200})()

    async def reject_identity(*_args, **_kwargs):
        raise OddsMatchIdentityError("MATCH_PROVIDER_MISMATCH")

    async def locked(_db, _resource_type, _resource_identity, operation):
        return True, await operation()

    _allow_odds_retry(monkeypatch)
    service = matches_api.football_service.odds_sync_service
    monkeypatch.setattr(service, "prepare_refresh_attempt", reject_identity)
    monkeypatch.setattr(service.retry_state_repository, "begin_attempt", begin_attempt)
    monkeypatch.setattr(service.retry_state_repository, "record_failure", record_failure)
    monkeypatch.setattr(
        service,
        "refresh_odds",
        AsyncMock(side_effect=lambda *_args, **_kwargs: provider_calls.append(True)),
    )
    monkeypatch.setattr(matches_api, "_assert_match_allowed", allowed_match)
    monkeypatch.setattr(matches_api, "async_session", lambda: Session(db))
    monkeypatch.setattr(matches_api, "run_with_resource_lock", locked)

    try:
        asyncio.run(matches_api.sync_match_odds_route(100, db))
    except HTTPException as exc:
        assert exc.status_code == 422
        assert exc.detail == {
            "status": IDENTITY_REJECTED,
            "reason": "MATCH_PROVIDER_MISMATCH",
        }
    else:
        raise AssertionError("expected identity rejection")

    begin_attempt.assert_not_awaited()
    record_failure.assert_not_awaited()
    assert provider_calls == []


def test_manual_provider_response_identity_rejection_resolves_attempt_without_failure(monkeypatch):
    db = DB()
    record_failure = AsyncMock()
    resolve_attempt = AsyncMock()
    cache_calls = []

    async def allowed_match(_match_id, _db):
        return type("MatchRef", (), {"provider_fixture_id": 200})()

    async def fake_refresh(*_args, **_kwargs):
        return {
            "status": IDENTITY_REJECTED,
            "success": False,
            "reason": "PROVIDER_FIXTURE_ID_MISMATCH",
        }

    async def locked(_db, _resource_type, _resource_identity, operation):
        return True, await operation()

    class Cache:
        async def delete(self, key):
            cache_calls.append(key)
            return True

    _allow_odds_retry(monkeypatch)
    service = matches_api.football_service.odds_sync_service
    monkeypatch.setattr(service.retry_state_repository, "record_failure", record_failure)
    monkeypatch.setattr(service, "resolve_attempt_without_candidate", resolve_attempt)
    monkeypatch.setattr(service, "refresh_odds", fake_refresh)
    monkeypatch.setattr(matches_api, "_assert_match_allowed", allowed_match)
    monkeypatch.setattr(matches_api, "async_session", lambda: Session(db))
    monkeypatch.setattr(matches_api, "run_with_resource_lock", locked)
    monkeypatch.setattr(matches_api, "CacheService", Cache)

    try:
        asyncio.run(matches_api.sync_match_odds_route(100, db))
    except HTTPException as exc:
        assert exc.status_code == 422
    else:
        raise AssertionError("expected provider fixture identity rejection")

    resolve_attempt.assert_awaited_once()
    record_failure.assert_not_awaited()
    assert cache_calls == []


def test_manual_identity_lookup_failure_returns_safe_503(monkeypatch):
    db = DB()

    async def allowed_match(_match_id, _db):
        return type("MatchRef", (), {"provider_fixture_id": 200})()

    async def fail_identity_lookup(*_args, **_kwargs):
        raise OddsIdentityLookupError("database details must not be exposed")

    async def locked(_db, _resource_type, _resource_identity, operation):
        return True, await operation()

    _allow_odds_retry(monkeypatch)
    service = matches_api.football_service.odds_sync_service
    monkeypatch.setattr(service, "prepare_refresh_attempt", fail_identity_lookup)
    monkeypatch.setattr(matches_api, "_assert_match_allowed", allowed_match)
    monkeypatch.setattr(matches_api, "async_session", lambda: Session(db))
    monkeypatch.setattr(matches_api, "run_with_resource_lock", locked)

    try:
        asyncio.run(matches_api.sync_match_odds_route(100, db))
    except HTTPException as exc:
        assert exc.status_code == 503
        assert exc.detail == "Odds Match identity unavailable"
        assert "database details" not in str(exc.detail)
    else:
        raise AssertionError("expected safe identity lookup failure")

    assert db.rollback_calls >= 1


def _assert_scheduler_identity_failure_rolls_back_before_next_eligible_match(
    monkeypatch,
    identity_error,
    expected_status,
):
    scheduler = LiveUpdateScheduler()
    events = []

    class TwoMatchDB(DB):
        async def execute(self, _statement):
            self.queries += 1
            if self.queries == 1:
                now = datetime.now(timezone.utc)
                return Result(
                    rows=[
                        (100, 200, "NS", now),
                        (101, 201, "NS", now),
                    ]
                )
            return Result(first_row=None)

        async def rollback(self):
            self.rollback_calls += 1
            self.operations.append("rollback")
            events.append("outer_db_rollback")

    db = TwoMatchDB()
    provider_calls = []
    retry_failure_calls = []
    lock_results = []
    service = scheduler_module.football_service.odds_sync_service

    async def prepare_attempt(_db, local_match_id, _provider_fixture_id):
        events.append(f"prepare:{local_match_id}")
        if local_match_id == 100:
            events.append("first_match_identity_error")
            raise identity_error
        return "attempt-101"

    async def fake_refresh(_db, provider_fixture_id, _cache_key, _ttl, local_match_id, **_kwargs):
        events.append(f"provider_request:{local_match_id}")
        provider_calls.append((local_match_id, provider_fixture_id))
        candidate = _candidate_result()
        candidate["_transaction_context"]["local_match_id"] = local_match_id
        candidate["_transaction_context"]["provider_fixture_id"] = provider_fixture_id
        return candidate

    async def locked(_db, _resource_type, _resource_identity, operation):
        result = await operation()
        lock_results.append(result)
        return True, result

    async def record_retry_failure(*_args, **_kwargs):
        retry_failure_calls.append(True)
        return True

    _allow_odds_retry(monkeypatch)
    scheduler.cache_service = Cache()

    async def eligible(_db, local_match_id):
        events.append(f"eligible:{local_match_id}")
        return {
            "eligible": True,
            "status": "ELIGIBLE",
            "cache_invalidation_pending": False,
            "retry_due": False,
        }

    monkeypatch.setattr(service, "check_retry_eligibility", eligible)
    monkeypatch.setattr(service, "prepare_refresh_attempt", prepare_attempt)
    monkeypatch.setattr(service, "refresh_odds", fake_refresh)
    monkeypatch.setattr(
        service.odds_repository,
        "get_fixture_odds",
        lambda *_args: asyncio.sleep(0, result=[]),
    )
    monkeypatch.setattr(scheduler_module, "async_session", lambda: Session(db))
    monkeypatch.setattr(scheduler_module, "run_with_resource_lock", locked)
    monkeypatch.setattr(scheduler_module, "persist_odds_retry_failure", record_retry_failure)

    result = asyncio.run(scheduler._refresh_odds_job())

    assert result["failed_matches"] == 1
    assert result["refreshed_matches"] == 1
    assert lock_results[0]["status"] == expected_status
    identity_error_index = events.index("first_match_identity_error")
    identity_rollback_index = events.index("outer_db_rollback", identity_error_index + 1)
    next_match_index = events.index("eligible:101")
    assert identity_error_index < identity_rollback_index < next_match_index
    assert next_match_index < events.index("prepare:101")
    assert events.index("prepare:101") < events.index("provider_request:101")
    assert provider_calls == [(101, 201)]
    assert db.rollback_calls >= 3
    assert retry_failure_calls == []


def test_scheduler_identity_lookup_failure_rolls_back_before_next_eligible_match(monkeypatch):
    _assert_scheduler_identity_failure_rolls_back_before_next_eligible_match(
        monkeypatch,
        OddsIdentityLookupError("database unavailable"),
        IDENTITY_UNAVAILABLE,
    )


def test_scheduler_identity_rejection_rolls_back_before_next_eligible_match(monkeypatch):
    _assert_scheduler_identity_failure_rolls_back_before_next_eligible_match(
        monkeypatch,
        OddsMatchIdentityError("MATCH_PROVIDER_MISMATCH"),
        IDENTITY_REJECTED,
    )


def test_odds_retry_after_and_scheduler_fallback_policy():
    service = scheduler_module.football_service.odds_sync_service
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    fallback = service.retry_state_update(
        {"status": PROVIDER_FAILED, "reason": "RATE_LIMITED"},
        now,
    )
    assert fallback["next_eligible_at"] == now + timedelta(hours=3)
    assert fallback["provider_cooldown_until"] == now + timedelta(hours=3)

    retry_after = service.retry_state_update(
        {
            "status": PROVIDER_FAILED,
            "reason": "RATE_LIMITED",
            "http_status": 429,
            "retry_after": "14400",
        },
        now,
    )
    assert retry_after["next_eligible_at"] == now + timedelta(hours=4)
    assert retry_after["provider_cooldown_until"] == now + timedelta(hours=4)

    timeout = service.retry_state_update(
        {"status": PROVIDER_FAILED, "reason": "TIMEOUT"},
        now,
    )
    assert timeout["next_eligible_at"] == now + timedelta(hours=3)
    assert timeout["provider_cooldown_until"] is None


def test_retry_eligibility_blocks_unknown_commit_and_honors_durable_cooldown(monkeypatch):
    service = scheduler_module.football_service.odds_sync_service
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)

    class RetryRepo:
        async def get_by_match_id(self, _db, _match_id):
            return SimpleNamespace(
                last_failure_classification=COMMIT_UNKNOWN,
                next_eligible_at=None,
                cache_invalidation_pending=True,
            )

        async def get_provider_cooldown(self, _db, _provider_key):
            raise AssertionError("COMMIT_UNKNOWN must block before provider cooldown lookup")

    monkeypatch.setattr(service, "retry_state_repository", RetryRepo())
    blocked = asyncio.run(service.check_retry_eligibility(DB(), 100, now))
    assert blocked["status"] == RETRY_BLOCKED
    assert blocked["reason"] == COMMIT_UNKNOWN
    assert blocked["cache_invalidation_pending"] is True

    class CooldownRepo:
        async def get_by_match_id(self, _db, _match_id):
            return None

        async def get_provider_cooldown(self, _db, _provider_key):
            return SimpleNamespace(cooldown_until=now + timedelta(minutes=10))

    monkeypatch.setattr(service, "retry_state_repository", CooldownRepo())
    cooldown = asyncio.run(service.check_retry_eligibility(DB(), 101, now))
    assert cooldown["status"] == RETRY_BLOCKED
    assert cooldown["reason"] == "PROVIDER_COOLDOWN"


def test_retry_state_write_failure_uses_volatile_match_and_provider_blocks():
    service = scheduler_module.football_service.odds_sync_service
    service.volatile_blocked_matches.clear()
    service.volatile_provider_cooldown_until = None
    service.mark_retry_state_unavailable(
        100,
        RuntimeError("retry state unavailable"),
        datetime.now(timezone.utc) + timedelta(hours=1),
    )

    async def unexpected_read(*_args, **_kwargs):
        raise AssertionError("volatile block should prevent retry-state reads")

    original_repository = service.retry_state_repository
    service.retry_state_repository = SimpleNamespace(
        get_by_match_id=unexpected_read,
        get_provider_cooldown=unexpected_read,
    )
    try:
        local_block = asyncio.run(service.check_retry_eligibility(DB(), 100))
        provider_block = asyncio.run(service.check_retry_eligibility(DB(), 101))
    finally:
        service.retry_state_repository = original_repository
        service.volatile_blocked_matches.clear()
        service.volatile_provider_cooldown_until = None

    assert local_block["status"] == RETRY_BLOCKED
    assert provider_block["reason"] == "PROVIDER_COOLDOWN"


def test_manual_and_scheduled_sync_share_retry_cooldown_gate(monkeypatch):
    service = scheduler_module.football_service.odds_sync_service
    provider_calls = []
    _allow_odds_retry(monkeypatch)

    async def cooldown(*_args, **_kwargs):
        return {
            "eligible": False,
            "status": RETRY_BLOCKED,
            "reason": "PROVIDER_COOLDOWN",
            "cache_invalidation_pending": False,
        }

    async def fake_refresh(*_args, **_kwargs):
        provider_calls.append(True)
        return _candidate_result()

    async def locked(_db, _resource_type, _resource_identity, operation):
        return True, await operation()

    monkeypatch.setattr(service, "check_retry_eligibility", cooldown)
    monkeypatch.setattr(service, "refresh_odds", fake_refresh)
    monkeypatch.setattr(scheduler_module, "run_with_resource_lock", locked)
    scheduler_db = DB()
    monkeypatch.setattr(scheduler_module, "async_session", lambda: Session(scheduler_db))
    scheduled = asyncio.run(LiveUpdateScheduler()._refresh_odds_job())
    assert scheduled["skipped_matches"] == 1

    async def allowed_match(_match_id, _db):
        return type("MatchRef", (), {"provider_fixture_id": 200})()

    monkeypatch.setattr(matches_api, "_assert_match_allowed", allowed_match)
    monkeypatch.setattr(matches_api, "run_with_resource_lock", locked)
    try:
        asyncio.run(matches_api.sync_match_odds_route(100, DB()))
    except HTTPException as exc:
        assert exc.status_code == 409
    else:
        raise AssertionError("manual Odds sync should honor provider cooldown")

    assert provider_calls == []


def test_manual_odds_retry_not_due_returns_json_409_with_iso_datetime(monkeypatch):
    db = DB()
    next_eligible_at = datetime(2026, 10, 11, 9, 26, 17, 285706, tzinfo=timezone.utc)
    service = matches_api.football_service.odds_sync_service

    async def allowed_match(_match_id, _db):
        return type("MatchRef", (), {"provider_fixture_id": 1557421})()

    async def retry_not_due(_db, _match_id):
        return {
            "eligible": False,
            "status": RETRY_BLOCKED,
            "reason": "RETRY_NOT_DUE",
            "next_eligible_at": next_eligible_at,
        }

    async def locked(_db, _resource_type, _resource_identity, operation):
        return True, await operation()

    async def override_db():
        yield db

    async def override_admin():
        return SimpleNamespace(role="admin", is_active=True)

    old_db_override = app.dependency_overrides.get(get_db)
    old_admin_override = app.dependency_overrides.get(get_current_active_admin)
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_active_admin] = override_admin
    monkeypatch.setattr(matches_api, "_assert_match_allowed", allowed_match)
    monkeypatch.setattr(service, "check_retry_eligibility", retry_not_due)
    monkeypatch.setattr(matches_api, "run_with_resource_lock", locked)
    try:
        response = TestClient(app).post("/api/matches/sync/1507/odds")
    finally:
        if old_db_override is None:
            app.dependency_overrides.pop(get_db, None)
        else:
            app.dependency_overrides[get_db] = old_db_override
        if old_admin_override is None:
            app.dependency_overrides.pop(get_current_active_admin, None)
        else:
            app.dependency_overrides[get_current_active_admin] = old_admin_override

    assert response.status_code == 409
    assert response.json() == {
        "detail": {
            "status": RETRY_BLOCKED,
            "success": False,
            "reason": "RETRY_NOT_DUE",
            "next_eligible_at": next_eligible_at.isoformat(),
        }
    }
    assert db.rollback_calls == 1


def test_scheduler_recovers_pending_cache_outside_provider_refresh(monkeypatch):
    scheduler = LiveUpdateScheduler()
    scheduler.pending_odds_cache_invalidations.add(999)
    db = DB()
    cache = Cache()
    service = scheduler_module.football_service.odds_sync_service
    calls = []

    async def pending_states(*_args, **_kwargs):
        return [SimpleNamespace(match_id=999)]

    async def pending_state(*_args, **_kwargs):
        return SimpleNamespace(
            cache_invalidation_pending=True,
            last_failure_classification=None,
            reconciliation_state="COMMIT_CONFIRMED_CACHE_PENDING",
            last_reconciliation_at=datetime.now(timezone.utc),
        )

    async def clear_pending(*_args, **_kwargs):
        return None

    async def blocked(*_args, **_kwargs):
        return {
            "eligible": False,
            "status": RETRY_BLOCKED,
            "reason": "RETRY_NOT_DUE",
            "cache_invalidation_pending": False,
        }

    async def unexpected_refresh(*_args, **_kwargs):
        raise AssertionError("pending-cache recovery must not fetch provider data")

    async def lock(_db, resource_type, resource_identity, operation):
        calls.append((resource_type, resource_identity))
        return True, await operation()

    monkeypatch.setattr(service.retry_state_repository, "get_pending_cache_invalidations", pending_states)
    monkeypatch.setattr(
        service.retry_state_repository,
        "get_pending_reconciliations",
        lambda *_args, **_kwargs: asyncio.sleep(0, result=[]),
    )
    monkeypatch.setattr(service.retry_state_repository, "get_by_match_id", pending_state)
    monkeypatch.setattr(service.retry_state_repository, "clear_cache_invalidation_pending", clear_pending)
    monkeypatch.setattr(service, "check_retry_eligibility", blocked)
    monkeypatch.setattr(service, "refresh_odds", unexpected_refresh)
    monkeypatch.setattr(scheduler_module, "run_with_resource_lock", lock)
    monkeypatch.setattr(scheduler_module, "async_session", lambda: Session(db))
    scheduler.cache_service = cache

    result = asyncio.run(scheduler._refresh_odds_job())

    assert result["skipped_matches"] == 1
    assert cache.deleted == ["fover:match:999:odds"]
    assert 999 not in scheduler.pending_odds_cache_invalidations
    assert calls[0] == ("odds", 999)


def test_scheduler_does_not_invalidate_cache_for_unknown_commit(monkeypatch):
    scheduler = LiveUpdateScheduler()
    scheduler.pending_odds_cache_invalidations.add(999)
    db = DB()
    cache = Cache()
    service = scheduler_module.football_service.odds_sync_service

    async def pending_states(*_args, **_kwargs):
        return [
            SimpleNamespace(
                match_id=999,
                last_failure_classification=COMMIT_UNKNOWN,
                cache_invalidation_pending=True,
            )
        ]

    async def pending_state(*_args, **_kwargs):
        return SimpleNamespace(
            cache_invalidation_pending=True,
            last_failure_classification=COMMIT_UNKNOWN,
            reconciliation_state="COMMIT_CONFIRMED_CACHE_PENDING",
            last_reconciliation_at=None,
        )

    async def unknown_commit(*_args, **_kwargs):
        return {
            "eligible": False,
            "status": RETRY_BLOCKED,
            "reason": COMMIT_UNKNOWN,
            "cache_invalidation_pending": True,
        }

    async def locked(_db, _resource_type, _resource_identity, operation):
        return True, await operation()

    monkeypatch.setattr(service.retry_state_repository, "get_pending_cache_invalidations", pending_states)
    monkeypatch.setattr(
        service.retry_state_repository,
        "get_pending_reconciliations",
        lambda *_args, **_kwargs: asyncio.sleep(0, result=[]),
    )
    monkeypatch.setattr(service.retry_state_repository, "get_by_match_id", pending_state)
    monkeypatch.setattr(service, "check_retry_eligibility", unknown_commit)
    monkeypatch.setattr(scheduler_module, "run_with_resource_lock", locked)
    monkeypatch.setattr(scheduler_module, "async_session", lambda: Session(db))
    scheduler.cache_service = cache

    result = asyncio.run(scheduler._refresh_odds_job())

    assert cache.deleted == []
    assert 999 in scheduler.pending_odds_cache_invalidations
    assert result["blocked_matches"] >= 1
