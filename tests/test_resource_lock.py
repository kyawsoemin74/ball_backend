import asyncio

import pytest

import app.services.resource_lock as resource_lock
from app.services.resource_lock import (
    ResourceLockAcquisitionError,
    acquire_resource_lock,
    build_resource_identity,
    run_with_resource_lock,
)


class FakeResult:
    def __init__(self, acquired):
        self.acquired = acquired

    def scalar_one(self):
        return self.acquired


class FakeDB:
    def __init__(self, acquired=True):
        self.acquired = acquired
        self.queries = []
        self.commit_calls = 0
        self.rollback_calls = 0

    async def execute(self, statement, params):
        self.queries.append((str(statement), params))
        if "pg_try_advisory_xact_lock" in str(statement):
            return FakeResult(self.acquired)
        return FakeResult(True)

    async def commit(self):
        self.commit_calls += 1

    async def rollback(self):
        self.rollback_calls += 1


class FailingResult:
    def scalar_one(self):
        raise RuntimeError("result unavailable")


def test_fixture_query_identity_is_exact_and_normalized():
    assert build_resource_identity("FIXTURE_QUERY", "GLOBAL") == "fover:sync:fixture_query:global"


def test_fixture_query_identity_does_not_create_surrogate_scopes():
    identity = build_resource_identity("fixture_query", "global")

    assert identity == "fover:sync:fixture_query:global"
    assert "daily" not in identity
    assert "season" not in identity
    assert "live" not in identity
    assert "repair" not in identity


def test_resource_identity_is_deterministic_and_match_identity_remains_compatible():
    assert build_resource_identity("fixture_query", "global") == build_resource_identity("FIXTURE_QUERY", "GLOBAL")
    assert build_resource_identity("match", 501) == "fover:sync:match:501"


def test_acquisition_uses_hashtextextended_with_canonical_identity():
    db = FakeDB()

    handle = asyncio.run(acquire_resource_lock(db, "FIXTURE_QUERY", "GLOBAL"))

    assert handle is not None
    assert "hashtextextended" in db.queries[0][0]
    assert db.queries[0][1] == {"lock_identity": "fover:sync:fixture_query:global"}


def test_contended_lock_returns_none_without_business_transaction_calls():
    db = FakeDB(acquired=False)

    handle = asyncio.run(acquire_resource_lock(db, "fixture_query", "global"))

    assert handle is None
    assert db.commit_calls == 0
    assert db.rollback_calls == 0


def test_run_with_resource_lock_does_not_execute_after_contention():
    db = FakeDB(acquired=False)
    calls = []

    async def operation():
        calls.append("operation")

    locked, result = asyncio.run(
        run_with_resource_lock(db, "fixture_query", "global", operation)
    )

    assert (locked, result) == (False, None)
    assert calls == []


def test_missing_execute_interface_fails_closed():
    calls = []

    async def operation():
        calls.append("operation")

    with pytest.raises(ResourceLockAcquisitionError) as error:
        asyncio.run(run_with_resource_lock(object(), "odds", 100, operation))

    assert isinstance(error.value.__cause__, TypeError)
    assert calls == []


def test_sql_execution_failure_is_not_contention():
    original_error = RuntimeError("database unavailable")

    class FailingDB(FakeDB):
        async def execute(self, _statement, _params):
            raise original_error

    calls = []

    async def operation():
        calls.append("operation")

    with pytest.raises(ResourceLockAcquisitionError) as error:
        asyncio.run(
            run_with_resource_lock(FailingDB(), "odds", 100, operation)
        )

    assert error.value.__cause__ is original_error
    assert calls == []


def test_result_reading_failure_is_not_contention():
    class FailingDB(FakeDB):
        async def execute(self, _statement, _params):
            return FailingResult()

    calls = []

    async def operation():
        calls.append("operation")

    with pytest.raises(ResourceLockAcquisitionError) as error:
        asyncio.run(
            run_with_resource_lock(FailingDB(), "odds", 100, operation)
        )

    assert isinstance(error.value.__cause__, RuntimeError)
    assert calls == []


def test_non_boolean_lock_result_is_not_contention():
    class InvalidResultDB(FakeDB):
        async def execute(self, _statement, _params):
            return FakeResult(None)

    with pytest.raises(ResourceLockAcquisitionError) as error:
        asyncio.run(acquire_resource_lock(InvalidResultDB(), "odds", 100))

    assert isinstance(error.value.__cause__, TypeError)


def test_cancelled_acquisition_propagates_without_running_operation():
    async def probe():
        execute_started = asyncio.Event()
        operation_calls = []

        class CancelledDB:
            async def execute(self, _statement, _params):
                execute_started.set()
                await asyncio.Event().wait()

        async def operation():
            operation_calls.append(True)

        task = asyncio.create_task(
            run_with_resource_lock(
                CancelledDB(),
                "fixture_query",
                "global",
                operation,
            )
        )
        await execute_started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert operation_calls == []

    asyncio.run(probe())


def test_transaction_lock_releases_after_successful_operation():
    db = FakeDB()
    calls = []

    async def operation():
        calls.append("operation")
        return "ok"

    locked, result = asyncio.run(run_with_resource_lock(db, "fixture_query", "global", operation))

    assert locked is True
    assert result == "ok"
    assert calls == ["operation"]
    assert any("pg_try_advisory_xact_lock" in query for query, _ in db.queries)


def test_callback_exception_propagates_and_scope_exit_runs(monkeypatch):
    db = FakeDB()
    scope_exits = []

    async def record_scope_exit(handle):
        scope_exits.append(handle.lock_key)

    monkeypatch.setattr(resource_lock, "release_resource_lock", record_scope_exit)

    async def operation():
        raise RuntimeError("sync failed")

    with pytest.raises(RuntimeError, match="sync failed"):
        asyncio.run(run_with_resource_lock(db, "fixture_query", "global", operation))

    assert any("pg_try_advisory_xact_lock" in query for query, _ in db.queries)
    assert scope_exits == ["fover:sync:fixture_query:global"]


def test_cancelled_callback_runs_scope_exit(monkeypatch):
    db = FakeDB()
    scope_exits = []

    async def record_scope_exit(handle):
        scope_exits.append(handle.lock_key)

    monkeypatch.setattr(resource_lock, "release_resource_lock", record_scope_exit)

    async def probe():
        entered = asyncio.Event()

        async def operation():
            entered.set()
            await asyncio.Event().wait()

        task = asyncio.create_task(
            run_with_resource_lock(db, "fixture_query", "global", operation)
        )
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(probe())
    assert scope_exits == ["fover:sync:fixture_query:global"]


def test_lock_abstraction_does_not_own_business_transaction():
    db = FakeDB()

    async def operation():
        return "ok"

    asyncio.run(run_with_resource_lock(db, "fixture_query", "global", operation))

    assert db.commit_calls == 0
    assert db.rollback_calls == 0