import asyncio

import pytest

from app.services.resource_lock import (
    acquire_resource_lock,
    build_resource_identity,
    release_resource_lock,
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
        if "pg_try_advisory_lock" in str(statement):
            return FakeResult(self.acquired)
        return FakeResult(True)

    async def commit(self):
        self.commit_calls += 1

    async def rollback(self):
        self.rollback_calls += 1


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


def test_unavailable_lock_returns_conflict_without_business_transaction_calls():
    db = FakeDB(acquired=False)

    handle = asyncio.run(acquire_resource_lock(db, "fixture_query", "global"))

    assert handle is None
    assert db.commit_calls == 0
    assert db.rollback_calls == 0


def test_release_runs_after_successful_operation():
    db = FakeDB()
    calls = []

    async def operation():
        calls.append("operation")
        return "ok"

    locked, result = asyncio.run(run_with_resource_lock(db, "fixture_query", "global", operation))

    assert locked is True
    assert result == "ok"
    assert calls == ["operation"]
    assert any("pg_advisory_unlock" in query for query, _ in db.queries)


def test_release_runs_when_operation_raises():
    db = FakeDB()

    async def operation():
        raise RuntimeError("sync failed")

    with pytest.raises(RuntimeError, match="sync failed"):
        asyncio.run(run_with_resource_lock(db, "fixture_query", "global", operation))

    assert any("pg_advisory_unlock" in query for query, _ in db.queries)


def test_lock_abstraction_does_not_own_business_transaction():
    db = FakeDB()

    async def operation():
        return "ok"

    asyncio.run(run_with_resource_lock(db, "fixture_query", "global", operation))

    assert db.commit_calls == 0
    assert db.rollback_calls == 0