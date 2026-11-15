# Normal `live_sync` Regression Verification

## 1. Executive Summary

This verification is a read-only regression check of the Normal `live_sync` implementation against the approved design freeze and the target-design audit. The implementation under test includes the terminal-protection and result-preservation changes in:

- `app/repositories/match_repository.py`
- `app/services/fixture_sync_service.py`
- `app/services/final_match_sync_service.py`

The evidence shows that the focused regression protections passed, but the scheduler-level regression set did not pass. The main failing area is the existing resource-lock path exercised by `tests/test_live_sync_phase6.py`: the fake DB sessions used by those tests do not provide `execute()`, and the scheduler calls `await db.execute(...)` when acquiring the lock. That is a test-fixture compatibility problem in the harness, not a confirmed production-code defect, but it is still an unresolved failing regression in the current verification gate.

Therefore, the current implementation is not yet verified as safe and regression-free under the required proof set. The final verdict is `FAIL`.

## 2. Worktree Baseline

Pre-existing state before verification (captured from `git status --short`):

- Modified tracked files:
  - `app/repositories/match_repository.py`
  - `app/services/final_match_sync_service.py`
  - `app/services/fixture_sync_service.py`
  - `tests/test_phase3d_event_sync.py`
- Untracked files already present:
  - `docs/API_FOOTBALL_NEWS_ENDPOINT_AUDIT.md`
  - `docs/FINAL_LIVE_SYNC_DESIGN_GAP_AUDIT.md`
  - `docs/FINAL_LIVE_SYNC_FINAL_DESIGN_COMPATIBILITY_AUDIT.md`
  - `docs/LIVE_SYNC_DESIGN_AUDIT.md`
  - `docs/NORMAL_LIVE_SYNC_DESIGN_FREEZE.md`
  - `docs/NORMAL_LIVE_SYNC_IMPLEMENTATION_REPORT.md`
  - `docs/NORMAL_LIVE_SYNC_TARGET_DESIGN_AUDIT.md`
  - `docs/PEN_STATUS_STALE_MATCH_AUDIT.md`

These files were preserved without modification. No source code, no tests, and no migrations were edited during this verification task beyond the creation of this verification report itself.

## 3. Implementation Diff Review

The implementation reviewed is consistent with the approved frozen design in broad scope:

- Terminal status protection is enforced in the repository persistence layer rather than only in the service layer.
- `P` is kept non-terminal and is not converted to `PEN` automatically.
- The protected result tuple is preserved in the update/upsert path.
- The display-only metadata policy is reflected in the repository/upsert logic.
- The per-match lock and transaction behavior remain aligned with the architecture-preservation requirement.

The implementation does not introduce a new architecture, table, migration, scheduler configuration change, or provider bypass. It remains within the approved design boundary.

The unresolved issue is not a broad design mismatch; it is the scheduler test harness mismatch around the resource-lock acquisition contract. The production code and/or the test harness require an explicit contract alignment.

## 4. Regression Matrix

| Area | Result | Evidence |
|---|---|---|
| Terminal Status Protection | PASS | Repository-level stale-write protections and terminal-aware update logic were exercised by the focused regression tests and passed. |
| Result Integrity | PASS | Finalized status/score/elapsed tuple protection passed in repository and upsert regression tests. |
| Metadata Policy | PASS | Finalized metadata preservation logic was included in the intended upsert behavior and passed targeted tests. |
| All Database Writers | PASS (with limits) | The repository and fixture-upsert writers were reviewed and the targeted regression tests passed. |
| Per-Match Locking | FAIL (evidence: scheduler test harness mismatch) | The failing scheduler tests show the fake DB session does not implement `execute()`, which is required by `acquire_resource_lock()` in `app/services/resource_lock.py`. |
| Atomic Persistence | PASS (evidence from regression tests) | Stale terminal writes are rejected in the repository path and in fixture upsert logic. |
| Existing Behavior Compatibility | INCOMPLETE / FAILING GATE | Focused regressions passed, but scheduler regression tests failed and therefore the full compatibility gate is not clean. |
| Architecture Compliance | PASS | No duplicate architecture was introduced; the frozen Provider → Service → SyncService → Repository → Database flow was preserved. |

## 5. Test Results

### 5.1 Focused regression command and result

Command executed:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_match_repository_live_stale.py tests/test_match_upsert_on_conflict.py tests/test_live_sync_phase6.py tests/test_final_match_sync_phase3.py tests/test_phase3d_event_sync.py tests/test_sync_boundary_freeze.py -q --tb=no
```

Result:

- `124 passed`
- `8 failed`
- Exit code: `1`

The failures were all in `tests/test_live_sync_phase6.py` and all showed the same root cause around the resource-lock acquisition path.

### 5.2 Static checks

Command executed:

```powershell
git diff --check; .\.venv\Scripts\python.exe -m py_compile app\repositories\match_repository.py app\services\fixture_sync_service.py app\services\final_match_sync_service.py
```

Result:

- `git diff --check`: no issues
- Python compile check: passed
- Exit code: `0`

## 6. Previously Failed 8 Scheduler Tests

Exact failing tests:

1. `tests/test_live_sync_phase6.py::test_existing_live_match_reaches_provider_boundary`
2. `tests/test_live_sync_phase6.py::test_commit_match_cache_order_and_lock_release`
3. `tests/test_live_sync_phase6.py::test_failed_transaction_does_not_publish_derived_state`
4. `tests/test_live_sync_phase6.py::test_live_sync_publishes_active_match_updates_after_commit`
5. `tests/test_live_sync_phase6.py::test_registry_failure_after_commit_does_not_rollback_match_state`
6. `tests/test_live_sync_phase6.py::test_redis_failure_after_commit_does_not_rollback`
7. `tests/test_live_sync_phase6.py::test_retry_after_provider_failure`
8. `tests/test_live_sync_phase6.py::test_phase5_ordering_regression_remains_explicit`

### 6.1 Full relevant traceback excerpt

```text
ERROR    app.services.resource_lock:resource_lock.py:65 RESOURCE_LOCK_ACQUIRE_FAILED lock_identity=fover:sync:live_sync:global
Traceback (most recent call last):
  File "D:\fover_backend\app\services\resource_lock.py", line 50, in acquire_resource_lock
    raise TypeError("database session does not provide execute()")
TypeError: database session does not provide execute()
ERROR    app.services.scheduler:scheduler.py:956 Error in live sync job: Resource lock acquisition failed
```

This single traceback appears across all eight failures. The failing assertions then show the job never reached the expected provider call or commit/registry/cache ordering:

```text
E       AssertionError: assert [] == ['provider']
...
E       AssertionError: assert ['lock_acquired', 'lock_released'] == ['lock_acquired', 'sync', 'commit', 'registry', 'cache', 'lock_released']
...
E       ValueError: list.index(x): x not in list
```

### 6.2 Actual failure cause

`app/services/resource_lock.py` requires the lock acquisition path to do:

```python
if not callable(getattr(db, "execute", None)):
    raise TypeError("database session does not provide execute()")
result = await db.execute(text(_ADVISORY_LOCK_SQL), {"lock_identity": lock_key})
```

The scheduler tests use a fake database object that does not implement `execute()`. The failing tests therefore never reach the intended `sync_live()` body and the job is aborted before provider invocation or post-commit steps.

### 6.3 Classification

This is a test-fixture compatibility issue, not a confirmed production defect, because the failure is triggered by a test double that does not satisfy the real contract required by `acquire_resource_lock()`. However, this is not harmless: the current test harness is failing to exercise the intended scheduler locking behavior around a real SQLAlchemy session. The failure is thus unresolved and prevents a clean regression declaration.

### 6.4 Intended behavior check

The tests are exercising the intended locking behavior in principle: they are verifying that the scheduler acquires a resource lock, runs sync, commits, updates registry/cache, and releases the lock. The issue is that the fake DB object does not implement the contract used by the production lock helper. So the tests are valid in intent, but they are not currently compatible with the production lock implementation.

## 7. Remaining Risks

- The scheduler test harness remains non-compliant with the real lock contract (`db.execute()` is required).
- A real PostgreSQL integration check was not performed in this verification task; therefore the production lock behavior under real DB sessions is not directly verified here.
- The focused regression tests passed, but the required scheduler tests did not, so the implementation cannot be declared fully regression-free.
- The provider and live-update architecture were preserved, but there is an unresolved discrepancy between the mocked lock interface and the production lock implementation.

## 8. Final Verdict

`FAIL`

Reason:

- The implementation preserves the approved architecture and passes the targeted functional protections.
- However, the required scheduler regression set did not pass.
- The failing tests are directly caused by the fake DB session not providing `execute()`, which is required by `acquire_resource_lock()` in `app/services/resource_lock.py` and used by the scheduler in `app/services/scheduler.py`.
- That failure is not a confirmed production bug in the database layer, but it is also not a clean pass; the verification gate still fails and therefore the implementation is not yet safe to authorize for broader change.

## 9. Final Status

- Design freeze compliance: `PASS` (as implemented architecture level)
- Terminal-result protection: `PASS` (in focused regression coverage)
- Scheduler lock verification: `FAIL` (under the current test harness)
- Overall verification result: `FAIL`

`DESIGN STATUS: APPROVED & FROZEN`

`IMPLEMENTATION STATUS: VERIFIED AS PARTIALLY SAFE, BUT REGRESSION GATE FAILS`

`REFERENCE AUDIT: READY AFTER DESIGN ADJUSTMENT`
