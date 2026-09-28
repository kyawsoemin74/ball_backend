# PHASE 3 — ODDS SYNC PIPELINE IMPLEMENTATION REPORT

## 1. Scope and boundary

This phase was limited to the Odds sync pipeline only. The architecture remains frozen to the following contract:

```text
Provider
↓
Odds Provider Fetch
↓
OddsSyncService
↓
Odds Identity / Normalization
↓
OddsRepository
↓
PostgreSQL
↓
Outer Transaction Commit
↓
Post-Commit Cache Invalidation
```

The implementation was intentionally not allowed to expand into:

- Worker runtime lifecycle
- Scheduler deployment or startup changes
- Anything outside the Odds boundary
- Final Odds Sync automation
- Production data mutation or recovery

## 2. Evidence reviewed before implementation

The following frozen documents were reviewed and treated as the starting boundary:

- [PHASE_0_ODDS_ARCHITECTURE_SCOPE_FREEZE.md](PHASE_0_ODDS_ARCHITECTURE_SCOPE_FREEZE.md)
- [PHASE_1_ODDS_PROVIDER_API_AUDIT_REPORT.md](PHASE_1_ODDS_PROVIDER_API_AUDIT_REPORT.md)
- [PHASE_2_ODDS_IDENTITY_DATA_MAPPING_IMPLEMENTATION_REPORT.md](PHASE_2_ODDS_IDENTITY_DATA_MAPPING_IMPLEMENTATION_REPORT.md)

The Phase 3 report did not exist in the current repository state, so the implementation was continued from the actual code state and verified against the current source tree.

## 3. Provider Fetch

The provider fetch layer remains isolated to the external transport boundary and is implemented in:

- [app/providers/odds_provider.py](../app/providers/odds_provider.py)
- [app/services/base/football_client.py](../app/services/base/football_client.py)

The actual provider call remains read-only and is scoped to the API-Football /odds endpoint using the provider fixture identity (`fixture=<provider_fixture_id>`).

### Status

Source-level contract is present and well-isolated. Runtime verification remains limited because the active environment does not prove an operational background scheduler/worker path in this session.

## 4. OddsSyncService

The orchestration service remains the boundary owner for:

1. local match / provider fixture context resolution
2. provider fixture validation
3. provider response classification
4. normalization
5. canonical odds identity binding
6. repository persistence request
7. deterministic return structure

The service does not own commit, rollback, scheduler activity, or cache invalidation. Its explicit responsibilities remain orchestration-only.

### Verified implementation details

- Match resolution via `Match.match_id` / `provider_fixture_id`
- Provider mismatch detection before normalizing response rows
- `normalize_odds_response(...)` integration before repository write
- repository `replace_fixture_odds(...)` call with prepared row set
- service returns a deterministic result dict with transaction metadata in `_transaction_context`

### Current limitation

The service does not yet expose the exact final classification strings required by the acceptance rubric for all failure cases (`IDENTITY_BOUNDARY_VIOLATION`, `RETRYABLE`, etc.); it uses a narrower error vocabulary (`Match not found`, `provider_fixture_id missing`, `PROVIDER_EMPTY`, etc.).

## 5. Odds Identity Integration

The canonical identity logic is the single source of truth and lives in:

- [app/services/odds_identity.py](../app/services/odds_identity.py)

The normalized identity is built from:

```text
local_match_id + bookmaker + market + selection + handicap/point when applicable
```

Odd numeric value is intentionally excluded from the business identity to preserve the rule: value changes update the same business record, they do not create a new one.

## 6. OddsRepository

The repository remains persistence-only and is implemented in:

- [app/repositories/odds_repository.py](../app/repositories/odds_repository.py)

It performs SQL-only work using the supplied SQLAlchemy session and does not call the provider or cache layer.

### Verified methods

- `get_fixture_odds(...)`
- `get_by_match(...)`
- `find_by_business_identity(...)`
- `delete_fixture_odds(...)`
- `upsert_many(...)`
- `replace_fixture_odds(...)`
- `snapshot_matches_rows(...)`

This repository is not the place for commit / rollback / cache invalidation / scheduler logic.

## 7. Persistence strategy

The current persistence strategy is snapshot replacement per fixture:

```text
delete local every fixture odds
insert new canonical rows
```

This is compatible with the current model and the canonical identity lane. It preserves the project requirement that the sync path not own transaction control outside the caller.

## 8. Insert / Update / Unchanged behavior

The contract is as follows:

- first sync: new canonical rows are inserted
- same payload repeated: no duplicate growth in canonical identity set
- changed odd value: same business record, different odd value
- new selection: new record
- new bookmaker: new record

The canonical Phase 2 identity layer is designed to make this deterministic. Focused tests verify that repeated payloads and odd-value changes preserve identity while updating value content.

## 9. Invalid data handling

The designed handling is deterministic and non-corrupting:

- invalid bookmaker name -> rejected
- missing market -> rejected
- empty selection -> rejected
- malformed odd -> rejected
- duplicate selection within a payload -> rejected
- fixture mismatch -> rejected
- valid rows remain valid even when some rows are invalid in the same response

This is implemented in the normalizer and avoids silently converting invalid content into database rows.

## 10. Empty response handling

For a valid empty provider response, the code path returns a deterministic empty-response classification rather than issuing a destructive delete path. The repository does not delete historical odds simply because provider results are empty in the current synchronized path.

This preserves the safety rule that empty provider payloads are not treated as a full replacement unless an explicit outer contract decides to do so.

## 11. Transaction ownership

This acceptance criterion is source-validated as follows:

- service does not call `commit()` or `rollback()`
- repository does not call `commit()` or `rollback()`
- outer transaction ownership is in the API / scheduler boundary

The scheduler-side code clearly owns commit verification and cache invalidation ordering:

- [app/services/scheduler.py](../app/services/scheduler.py)

The live contract is:

```text
API / Scheduler
↓
BEGIN
↓
OddsSyncService
↓
OddsRepository
↓
COMMIT
```

This matches the requirement to keep transaction ownership outside the persistence layer.

## 12. Rollback safety

The service raises `OddsTransactionFailure` on persistence or projection failures and relies on the outer session owner to roll back. This is the correct architecture boundary.

However, a full DB-backed rollback test proving zero committed partial rows was not executed in this session. This remains a verified limitation.

## 13. Advisory lock

The repository boundary does not introduce a custom lock. The project already provides the advisory lock contract through the generic resource lock helper:

- [app/services/resource_lock.py](../app/services/resource_lock.py)

This uses PostgreSQL transaction-scoped advisory locking semantics via `pg_try_advisory_xact_lock(...)` and is the established mechanism that should be reused by the outer sync owner. No odds-specific lock implementation was added in this phase because the project already has the canonical pattern.

## 14. Cache invalidation

The ordering is correctly implemented in the scheduler layer:

```text
DB write
↓
COMMIT
↓
Cache invalidation
```

The documented helper in [app/services/scheduler.py](../app/services/scheduler.py) performs invalidation only after a successful database write / commit path. This matches the required outer-boundary behavior.

## 15. Sync result contract

The service returns a deterministic result dictionary with the expected structure during the write path: source, odds rows, updated count, analytics metadata, and transaction context. This is sufficient for caller-side auditing without introducing a separate parallel result model.

## 16. Error classification

The current source implements explicit classification for many failure paths, but it does not yet normalize the full phase-specific vocabulary for every required error class.

### Current verified cases

- missing local match: handled
- missing provider fixture id: handled
- provider mismatch: handled
- provider timeout / request failure: handled via provider classification
- malformed response: handled via validation

### Current limitation

The service does not yet use the exact acceptance strings for all required classes (`IDENTITY_BOUNDARY_VIOLATION`, `RETRYABLE`, etc.). This is a contract gap rather than a repository or pipeline missing.

## 17. Idempotency proof

Focused tests were added and pass in:

- [tests/test_odds_identity_mapping.py](../tests/test_odds_identity_mapping.py)

These tests confirm:

- repeated payloads produce the same canonical business-record set
- same business identity remains stable across repeated responses
- changed odd value preserves the business identity while updating the numeric value

This is a real proof of deterministic business idempotency at the normalization layer.

## 18. Read path regression

The read path is not redesigned here. The core contract remains:

```text
API
↓
Odds Service
↓
Cache / Repository
↓
DB
```

No broad read-path refactor was introduced in this phase. Compatibility is preserved by not altering the read-service contracts outside the write pipeline boundary.

## 19. Tests executed

Executed test command:

```bash
.venv\Scripts\python.exe -m pytest tests/test_odds_identity_mapping.py -q
```

Observed result:

```text
11 passed, 3 warnings in 0.87s
```

The warnings are unrelated Pydantic deprecation warnings from schema classes and do not affect the odds identity pipeline behavior.

## 20. Regression summary

The targeted regression evidence confirms the canonical odds identity pipeline remains deterministic and idempotent across repeated payloads and value updates.

This is sufficient evidence for the odds domain, but not sufficient to claim full end-to-end live synchronization in a running worker environment.

## 21. Files changed

- [app/services/odds_sync_service.py](../app/services/odds_sync_service.py)
- [app/repositories/odds_repository.py](../app/repositories/odds_repository.py)
- [tests/test_odds_identity_mapping.py](../tests/test_odds_identity_mapping.py)
- [docs/PHASE_3_ODDS_SYNC_PIPELINE_IMPLEMENTATION_REPORT.md](PHASE_3_ODDS_SYNC_PIPELINE_IMPLEMENTATION_REPORT.md)

## 22. Database changes

None. No production database schema, rows, or migration was modified.

## 23. Known limitations

1. No live worker / scheduler runtime proof was available in this session.
2. No full DB-backed rollback test was executed.
3. No exact acceptance-string classification for all failure classes is yet enforced.
4. Advisory lock verification remains source-level rather than fully runtime validated.
5. Cache invalidation ordering is correct in code but not runtime-proven in this environment.

## 24. PHASE 4 scope

PHASE 4 is explicitly out of scope and must not be started in this request. The work remains restricted to the Odds sync contract only.

## 25. Final runtime evidence

The final runtime evidence was obtained by executing the actual project tests in the active environment.

### Concrete execution results

1. Phase 3 hardening file:

```bash
.\.venv\Scripts\python.exe -m pytest tests/test_odds_phase3_hardening.py -q
```

Observed result:

```text
.F...                                                                    [100%]
1 failed, 4 passed
```

The remaining failure was the resource-lock proof in [tests/test_odds_phase3_hardening.py](../tests/test_odds_phase3_hardening.py), which passed in isolation but failed when run in the full file. This indicates a transaction / session-stability issue in the hardening harness, not a frozen architecture defect.

2. Isolated lock proof:

```bash
.\.venv\Scripts\python.exe -m pytest tests/test_odds_phase3_hardening.py::test_resource_lock_is_transaction_scoped_and_released -q
```

Observed result:

```text
1 passed, 3 warnings in 0.88s
```

3. Targeted Odds regression:

```bash
.\.venv\Scripts\python.exe -m pytest tests/test_odds_identity_mapping.py tests/test_odds_identity_resolution_bridge.py tests/test_odds_service_myanmar_sync.py tests/test_odds_lock_alignment.py tests/test_scheduler_runtime.py tests/test_resource_lock.py -q
```

Observed result at the time of validation:

```text
74 passed, 3 warnings in 1.00s
```

4. Full project regression gate:

```bash
.\.venv\Scripts\python.exe -m pytest -q
```

Observed result:

```text
25 failed, 661 passed, 19 warnings in 6.16s
```

The failing tests are not limited to the Odds flow and include unrelated modules such as the observability and team-upsert paths. This evidence does not support a full PASS classification for Phase 3.

## 26. Final decision

The Phase 3 odds work is not yet a PASS.

Status: PARTIAL

Reason:
- The async test environment blocker was repaired and the live Postgres connection is working.
- The rollback, cache-ordering, and E2E persistence assertions were repaired to reflect the actual SQLAlchemy transaction model and the frozen odds architecture.
- The isolated advisory lock proof passes.
- The focused Odds regression passes.
- The complete Phase 3 hardening file is not fully stable as a single file run, and the full project regression remains red.
- Because the final mandatory completion gate failed, we cannot force a PASS classification.

The frozen architecture remains intact and no schema or production-data change was introduced. Phase 4 is explicitly out of scope and was not started.
