# Normal `live_sync` Finalized-Result Protection — Implementation Report

## 1. Summary

Implemented the approved protection in the existing Provider → Service → SyncService → Repository → Database architecture. Ordinary live and fixture writers can no longer replace a finalized result with stale data. Exact-ID Finalization Sync remains the only writer authorized to refresh a terminal result after validating a provider terminal response.

The approved metadata policy is also enforced for finalized fixture rows: identity and scheduling fields are preserved, while display metadata may continue to refresh. No database schema, migration, scheduler configuration, or resource-lock implementation was changed.

## 2. Persistence Changes

In [`app/repositories/match_repository.py`](../app/repositories/match_repository.py):

- Defined the protected terminal set as `FT`, `AET`, and `PEN`; `P` remains non-terminal.
- Made `update_live_state` atomically reject ordinary writes when the stored status is terminal. It returns distinct `UPDATED`, `PROTECTED`, and `MISSING` outcomes.
- Added terminal-aware upsert expressions for the result tuple: status, elapsed time, and home/away scores.
- Added terminal-aware metadata expressions. On finalized rows, league/team IDs, season, match time, venue ID (when supplied), and referee ID are preserved. Existing display fields—names, logos, venue name, and venue city—remain refreshable.
- The Match model has no penalty-score columns; no schema was added.

In [`app/services/fixture_sync_service.py`](../app/services/fixture_sync_service.py):

- Applied both protected upsert expressions to the existing fixture conflict-upsert path, covering Normal Live Sync's related fixture writers, including daily/repair and full-season sync.
- Used the upsert `RETURNING` values for the persisted local identity and status. Registry updates and finalization-candidate decisions now use persisted state rather than blindly publishing the incoming status.
- Added the shared per-match lock to live updates and existing-row fixture upserts. A rejected or contended update is not reported as a successful write.

In [`app/services/final_match_sync_service.py`](../app/services/final_match_sync_service.py):

- Added the same per-match lock around persistence and event synchronization, after provider retrieval and terminal-state validation.
- Authorized only this validated finalization path to refresh an already-terminal result.
- Read the finalization attempt state without holding its row lock across provider I/O; finalization-state writes occur under the match-result lock, avoiding a finalization-row/match-lock inversion with fixture writers.

The shared lock identity is `match_result:<local_match_id>`. Existing scheduler-level job locks remain unchanged.

## 3. Database and Architecture Scope

- No model or schema changes and no Alembic migration.
- No new worker, service, queue, table, or synchronization framework.
- Provider reads remain outside the per-match lock; the lock covers the protected persistence operation and remains transaction-scoped through commit or rollback.
- A new fixture row has no canonical local ID before insertion, so it cannot acquire this local-ID lock in advance. Any concurrent conflict is still protected by the atomic upsert expressions.

## 4. Tests

Focused persistence and synchronization regressions:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_match_repository_live_stale.py tests/test_match_upsert_on_conflict.py tests/test_final_match_sync_phase3.py tests/test_phase3d_event_sync.py tests/test_sync_boundary_freeze.py tests/test_live_sync_phase6.py::test_live_sync_reports_persisted_local_match_status tests/test_live_sync_phase6.py::test_live_sync_does_not_publish_stale_data_for_a_finalized_match tests/test_live_sync_phase6.py::test_live_sync_does_not_report_registry_update_when_match_update_fails -q
```

Result: **93 passed**, with 3 existing Pydantic deprecation warnings.

The expanded six-file regression set completed with **124 passed and 8 failed**. All eight failures are in existing scheduler-lock tests in `tests/test_live_sync_phase6.py`. Their fake database sessions do not implement `execute()`, while the scheduler currently acquires its resource lock through that method; the captured failure is `database session does not provide execute()`. The scheduler and its global-lock behavior were not changed in this implementation.

Tests cover all three protected statuses, stale `LIVE`/`P` results, valid transitions to `FT`/`AET`/`PEN`, result and metadata preservation, display-metadata refresh, lock identity use, and finalization behavior.

Several regression test files are ignored by the repository's `tests/*` rule and therefore do not appear in ordinary `git status`; they remain in the workspace and were included in the commands above. They have not been staged.

## 5. Verification Limits and Remaining Risks

- SQLite-backed tests verify the conditional update and upsert expressions. No live PostgreSQL concurrency or multi-worker integration test was run; resource locks are mocked in unit tests.
- No provider API, scheduler job, sync endpoint, production database, or deployed worker was accessed.
- Penalty scores are not stored on the canonical `Match` model. The separate H2H analytics projection was not changed; penalty-score integrity in that projection is outside these Match-row write guards.
- The provider supplies no verified monotonic snapshot/version in this path. This implementation prevents terminal-to-nonterminal regression; it cannot order competing non-terminal snapshots. Terminal-to-terminal refresh is limited to validated Finalization Sync.
- The eight scheduler-test failures above remain unresolved because they are outside the changed persistence paths.

## 6. Changed Files

Application code:

- [`app/repositories/match_repository.py`](../app/repositories/match_repository.py)
- [`app/services/fixture_sync_service.py`](../app/services/fixture_sync_service.py)
- [`app/services/final_match_sync_service.py`](../app/services/final_match_sync_service.py)

Regression tests:

- [`tests/test_match_repository_live_stale.py`](../tests/test_match_repository_live_stale.py)
- [`tests/test_match_upsert_on_conflict.py`](../tests/test_match_upsert_on_conflict.py)
- [`tests/test_final_match_sync_phase3.py`](../tests/test_final_match_sync_phase3.py)
- [`tests/test_phase3d_event_sync.py`](../tests/test_phase3d_event_sync.py)

No unrelated files, migrations, or configuration were intentionally changed.
