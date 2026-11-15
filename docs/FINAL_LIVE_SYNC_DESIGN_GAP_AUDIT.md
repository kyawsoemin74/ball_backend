# FINAL_LIVE_SYNC Design Gap Audit

**Audit type:** Read-only source and focused-test audit  
**Scope:** Existing Fover Backend final-match synchronization and the `P` → `PEN` transition  
**Verdict:** **DESIGN GAP FOUND — Existing design needs targeted improvements.**

## 1. Executive Summary

The code does not convert provider status `P` into `PEN`. It classifies `P` as live/waiting and only accepts a fresh provider status of `FT`, `AET`, or `PEN` as terminal. The active worker can keep checking a match that remains `P`, and it persists retry state across process restarts.

The principal reliability gap is the finite retry lifecycle. `P` responses and retryable failures receive at most five attempts. On the fifth non-terminal response or retryable failure, the persistent row becomes `EXHAUSTED`, receives no next retry time, and is omitted from future due queries. Candidate discovery also deliberately skips `EXHAUSTED`; no existing recovery job resets it. A match can therefore reach `PEN` after the last attempt and remain permanently unfinalized.

There is a second integrity risk: the normal live-feed synchronization writes status and scores without guarding against terminal-to-live regression. If a stale provider response returns `P` for a match already stored as `PEN`, the ordinary live sync can overwrite the canonical match status and scores even when its finalization row is already `SUCCESS`.

The method named `FixtureSyncService.final_live_sync` is not the active scheduled finalization worker in the inspected source. The scheduled path is `LiveUpdateScheduler` → `FinalMatchSyncService.sync_final_match`; the older `final_live_sync` helper is called only from `handle_terminal_transition`, while the normal daily and live fixture paths explicitly disable that transition mode.

**Runtime/DB limitation:** No live provider request, running service, production database query, or database metadata query was performed. Conclusions about persisted schema are based on the checked-in model and migration, not runtime database inspection. No code, database object, migration, configuration, or service was changed.

## 2. Current Architecture and Execution Flow

### 2.1 Active scheduled final-match flow

1. `LiveUpdateScheduler.start` registers `sync_live_matches` every 60 seconds with `max_instances=1` (`app/services/scheduler.py`, `start`, around lines 503–514).
2. `_sync_live_matches_job` takes the scheduler-wide PostgreSQL advisory lock, queries due rows from `MatchFinalizationRepository.get_due_match_ids`, and invokes `_finalize_match_sync_candidates` before the normal live sync (around lines 879–953).
3. `FixtureSyncService.sync_live_matches` fetches the provider live-fixture list. It updates known live matches. A local live match missing from that provider response is still a finalization candidate: the method obtains local live matches, ensures a persistent finalization row, and adds eligible rows to `final_match_sync_candidates` (around lines 1160–1290).
4. The scheduler processes those candidates after committing the live sync. It claims the row as `RUNNING`, increments and persists the attempt count, then calls `football_service.sync_final_match_state` (around lines 641–830).
5. `FootballService.sync_final_match_state` delegates to `FinalMatchSyncService.sync_final_match` (`app/services/football.py`, `sync_final_match_state`, lines 183–184).
6. `FinalMatchSyncService` fetches the exact provider fixture ID, checks the returned status, validates elapsed time and goals, updates the canonical match through `MatchRepository.update_live_state`, refreshes events, and marks the finalization row `SUCCESS` ( `app/services/final_match_sync_service.py`, `sync_final_match`, lines 44–248).
7. Scheduler commits successful work, then invalidates the live-match and event caches. Failures are persisted as `FAILED` with a retry time or `EXHAUSTED` without one.

**Architecture mapping:** The active route follows Provider (`FixtureProvider`) → Service read (`get_fixtures_by_ids`) → SyncService orchestration (`FinalMatchSyncService`, scheduler) → Repository (`MatchRepository`, `MatchFinalizationRepository`, event repository) → database.

### 2.2 `final_live_sync` helper path

`FixtureSyncService.final_live_sync` separately fetches one fixture by ID, accepts only the terminal statuses in `FINAL_LINEUP_TERMINAL_STATUSES`, persists the fixture through `_process_sync_with_candidates`, and returns a success/failure result (`app/services/fixture_sync_service.py`, `final_live_sync`, lines 288–370). It uses the resource lock namespace `fixture:<provider_fixture_id>`.

That helper is invoked by `handle_terminal_transition` (lines 379–430). However, the normal `sync_live_matches`, `sync_daily_fixtures`, and full-season sync paths set `_allow_terminal_transition = False` while processing (`fixture_sync_service.py`, approximately lines 1098–1127). No production source caller was found enabling it. The scheduler therefore relies on the durable `FinalMatchSyncService` path described above, not on this helper.

## 3. Source-Code Evidence

| Evidence | Source and behavior |
|---|---|
| Status classification | [`app/services/fixture_sync_service.py`](../app/services/fixture_sync_service.py#L38): `P` is in `LIVE_STATUSES`, `NON_TERMINAL_STATUSES`, and active-match registration; `FT`, `AET`, and `PEN` are terminal. |
| Durable state | [`app/models/match_finalization.py`](../app/models/match_finalization.py#L7) defines `PENDING`, `RUNNING`, `SUCCESS`, `FAILED`, and `EXHAUSTED`, plus attempt, retry-time, completion, and error fields. |
| Schema migration | [`alembic/versions/20261005_create_match_finalization.py`](../alembic/versions/20261005_create_match_finalization.py#L17) creates the persistent table, constraints, and retry index. Runtime application of this migration was not verified. |
| Candidate discovery | [`app/services/fixture_sync_service.py`](../app/services/fixture_sync_service.py#L1160) obtains live fixtures; lines 1252–1289 register terminal transitions and locally live matches missing from the provider feed. |
| Final status check | [`app/services/final_match_sync_service.py`](../app/services/final_match_sync_service.py#L13) defines terminal/waiting sets; lines 145–167 branch on provider status and validation. |
| Retry policy | [`app/repositories/match_finalization_repository.py`](../app/repositories/match_finalization_repository.py#L10) sets five attempts and retry delays; lines 115–160 persist retryable failure, waiting, and exhaustion states. |
| Due work | [`app/repositories/match_finalization_repository.py`](../app/repositories/match_finalization_repository.py#L67) selects due `PENDING`/`FAILED` and stale `RUNNING` rows, not `EXHAUSTED`. |
| Scheduler lock | [`app/services/scheduler.py`](../app/services/scheduler.py#L1582) acquires PostgreSQL session-level advisory locks on a pinned connection; lines 641–830 coordinate finalization. |
| Daily repair | [`app/services/scheduler.py`](../app/services/scheduler.py#L1039) syncs yesterday and today only. |
| Unconditional match update | [`app/repositories/match_repository.py`](../app/repositories/match_repository.py#L21) updates status, elapsed, and scores by local ID without a terminal-status condition. |

## 4. P → PEN Transition Audit

| Question | Finding |
|---|---|
| Is `P` non-terminal? | **CONFIRMED.** It is in `FINAL_MATCH_WAIT_STATUSES` and `LIVE_STATUSES`. |
| Is `PEN` terminal? | **CONFIRMED.** `PEN` is in `FINAL_MATCH_TERMINAL_STATUSES` and `FINAL_LINEUP_TERMINAL_STATUSES`. |
| Does it continue checking while status is `P`? | **CONFIRMED, while retry state remains eligible.** Each live cycle can register a locally live match, and the finalizer returns `WAITING` for `P`. The waiting schedule persists the next attempt. This stops at the retry limit. |
| Does it require provider confirmation? | **CONFIRMED.** The finalizer uses the fresh fixture-by-ID response status; it does not infer a final status from elapsed time. |
| Is `P` accidentally treated as final or converted to `PEN`? | **NOT FOUND in the finalizer.** `P` is explicitly a waiting status, and persisted terminal status comes from the validated provider status. |
| Can the transition be missed? | **CONFIRMED possible after exhaustion.** Once exhausted, no due query or candidate reactivation revisits the row. |
| Can terminal data be regressed to `P`? | **CONFIRMED as a code-path possibility.** Ordinary live sync calls `update_live_state` for provider fixtures without preventing a terminal row from being overwritten. This is conditional on a stale `P` appearing in the live provider response; no live provider observation was made. |

The provider-status meanings in the prompt are treated as the intended meanings. The implementation uses the status tokens as given; provider documentation/runtime semantics were not independently verified.

## 5. Retry and EXHAUSTED Audit

### Retry lifecycle

- `FINALIZATION_MAX_ATTEMPTS` is **5**.
- Retryable provider/persistence/event failures use delays of **15, 30, 60, and 120 minutes**. After attempt 5, retryable failures become `EXHAUSTED`.
- A provider response still showing `P` (or another recognized active state) returns `WAITING`. It is rescheduled after **5, 10, 15, and 30 minutes**; a fifth waiting response becomes `EXHAUSTED`.
- `mark_running` increments the attempt count and clears `next_retry_at`. Retry state, attempts, last-attempt time, and failure information are database fields.
- The due query accepts `PENDING`/`FAILED` rows whose retry time is due, and stale `RUNNING` rows after five minutes. It excludes `EXHAUSTED`.
- A temporary exception in the provider request is retryable for the explicitly caught network exception classes. Missing fixture responses are retryable. Invalid response structure, unknown status, invalid final match state, invalid identity, and similar non-retryable outcomes can exhaust immediately.
- A `P` waiting response clears error fields, so it is distinguishable by the `WAITING` result/log path but is not retained as a last failure reason after `mark_waiting`.

### `EXHAUSTED`

`EXHAUSTED` is effectively terminal in current code:

1. `mark_failure` sets it for non-retryable failures or when the attempt count reaches 5.
2. `mark_waiting` sets it when a non-terminal response occurs at attempt 5.
3. `get_due_match_ids` never selects it.
4. Live and terminal-transition candidate paths append only when the returned state is not `SUCCESS` or `EXHAUSTED`.
5. `ensure_pending` inserts a row only when none exists; it does not reset an existing `EXHAUSTED` row.
6. Daily Repair does not reset it.

**Impact — CRITICAL:** A later provider transition to `PEN` cannot restart that row through the existing candidate path. No recovery job for `EXHAUSTED` was found.

## 6. Historical Recovery Audit

- Daily Repair runs at **02:00 Myanmar time** and calls `sync_daily_fixtures` for yesterday and today. It is therefore date-bounded to those two provider dates.
- Daily Repair is not the same mechanism as finalization retry: it refreshes fixture data and can create finalization eligibility through fixture processing, but it does not itself run `_finalize_match_sync_candidates`. The 60-second scheduler consumes committed due rows separately.
- The live sync candidate sweep uses `MatchRepository.get_live_matches`, which filters by live status but has no match-date cutoff. A locally stored historical `P` row can therefore enter candidate discovery while still eligible.
- Durable due rows are also queried without match-date filtering, with a per-cycle limit of 100.
- There is no separate bounded historical backfill/recovery job for old unfinished records. The ongoing live sweep is not a bounded historical repair policy and cannot recover `EXHAUSTED`.

**Conclusion:** A historical match stuck in `P` may be recovered by the all-live-status sweep if its row is not exhausted and the scheduler continues running. An exhausted historical match is not recoverable through current code. Daily Repair alone does not reach dates older than yesterday.

## 7. Final Data Validation Audit

### Active durable finalizer

`FinalMatchSyncService._validated_state` requires:

- provider status already be one of `FT`, `AET`, `PEN`;
- elapsed time to be an integer, not a boolean, and non-negative;
- `goals` to be a dictionary;
- `goals.home` and `goals.away` to be non-boolean, non-negative integers.

Validation does **not** require or read `score.penalty.home` / `score.penalty.away`. `Match` stores only the canonical home/away score pair. Penalty scores exist in the analytics projection schema, but they are not part of this finalizer’s canonical match validation or update. No assumption is made that every `PEN` response includes penalty scores.

Missing/invalid elapsed or goals cause `INVALID_FINAL_MATCH_STATE` with `retryable: False`; the scheduler records `EXHAUSTED` immediately. Thus a transiently incomplete terminal response is not retried. The status has already passed the explicit terminal gate, but final scores/elapsed may be rejected.

### `FixtureSyncService.final_live_sync`

This helper verifies the fresh provider status is terminal and checks the aggregate persistence result. It does not impose the strict elapsed/score type/range validation used in `_validated_state`. Its shared fixture parser defaults missing elapsed and scores to zero (`fixture_sync_service.py`, `parse_fixture_to_match`, around lines 250–280). The direct helper’s failures are returned to its caller; it does not own the persistent retry row.

**Impact — HIGH/CRITICAL:** Invalid final state in the durable path is observable in the stored failure category but permanently exhausts the row. Missing penalty scores are not themselves a rejection condition.

## 8. Persistence and State Management Audit

- `MatchFinalization` is a persistent one-row-per-match model keyed by `match_id`, with `state`, `attempt_count`, `next_retry_at`, `last_attempted_at`, `completed_at`, and last-error fields.
- The migration includes state/count/completion constraints and an index on `(state, next_retry_at)`.
- Scheduler commits the `RUNNING` claim before making the provider request. On restart, a `RUNNING` row older than five minutes is marked retryable failure; attempt count is not reset.
- Retry and waiting transitions are committed. `SUCCESS` is committed before cache invalidation.
- There is no `EXHAUSTED` reset path. The row is retained, but future retries are not scheduled.
- Database updates in `FinalMatchSyncService` share the scheduler's `AsyncSession` transaction. If event sync returns a retryable failure after the Match update, the scheduler can commit the Match update and `FAILED` state together, then retry later. This is partial workflow completion, not a single all-or-nothing provider operation.
- The direct `final_live_sync` helper does not use `MatchFinalization` retry state.

**Atomicity limitation:** Transaction behavior is inferred from shared session usage and explicit scheduler commits/rollbacks. It was not verified against a running database. Provider calls are necessarily outside database atomicity.

## 9. Scheduler and Concurrency Audit

- `sync_live_matches` is scheduled every 60 seconds; APScheduler `max_instances=1` prevents overlapping instances in one scheduler process.
- The job also holds a PostgreSQL session advisory lock (`LIVE_MATCH_SYNC_LOCK_KEY`) on a pinned connection, serializing the live scheduler across processes sharing the database.
- Finalization records are read with `for_update=True`; the worker commits `RUNNING` before provider work. The scheduler's broader live lock and `live_sync:global` resource lock serialize the active scheduled route.
- Event refresh takes an `events:<match_id>` transaction advisory resource lock.
- Duplicate scheduled requests are therefore controlled in the current scheduler route; `SUCCESS` rows are skipped.
- The direct `final_live_sync` helper uses `fixture:<provider_fixture_id>`, not the active finalizer's `live_sync:global` lock. The direct transition path is disabled in the inspected normal sync wrappers, but if separately enabled/called, those two lock identities would not mutually exclude each other.
- A failed scheduler cycle is logged and the scheduler continues. Persisted due work can be retried on a subsequent cycle; `EXHAUSTED` cannot.

No evidence of two concurrently deployed production workers was obtained. The checked-in Kubernetes worker deployment/HPA specifies one replica, while the advisory lock provides cross-process protection if more than one scheduler is running.

## 10. Idempotency and Data Integrity

- The durable worker returns immediately for a `SUCCESS` record, and finalization state uses one row per match.
- Match persistence is an `UPDATE` by local match ID, not an insert, so retries do not duplicate match rows.
- Event persistence replaces that match's event rows. The transaction and event resource lock limit concurrent replacement.
- Cache invalidation follows successful commit and targets the live-match and match-events cache keys. Failed finalization does not invalidate these caches.
- A failed event refresh can leave the final match status/scores persisted alongside a `FAILED` finalization state; a later retry can update them again and finish.
- **Regression risk:** `MatchRepository.update_live_state` sets `status`, `elapsed`, and both scores unconditionally. `FixtureSyncService.sync_live_matches` calls it on returned provider live fixtures without checking whether the existing row is already terminal. A stale non-terminal `P` can therefore overwrite a persisted `PEN` (or `FT`/`AET`) state. This is not prevented by a `SUCCESS` finalization row because live-state update is separate from candidate filtering.
- The strict finalizer itself will not write `P`; its terminal allowlist prevents that. The regression concern is the ordinary live-feed write path.

## 11. Failure Scenario Results

| Scenario | Actual execution path and behavior | Expected / recovery | Evidence and result |
|---|---|---|---|
| **1. Five `P` responses, then `PEN`** | The first four `P` results are persisted as `PENDING` with waits of 5/10/15/30 minutes. The fifth `P` changes the row to `EXHAUSTED`. On the later `PEN`, due selection excludes the row and candidate registration skips it. | Continue a controlled retry until terminal confirmation. **Recovery after the fifth `P`: none found.** | `mark_waiting`, `get_due_match_ids`, and candidate filters; **CRITICAL gap**. |
| **2. `P`, then application restart before next retry** | The `PENDING` row and `next_retry_at` are persisted. After restart the scheduled worker queries due rows; the attempt resumes when due. For a `RUNNING` crash, stale recovery is after five minutes and uses existing attempt count. | Pending work should survive restart. **Recovery: yes, unless restart occurs after exhaustion or attempt 5 is reached.** | Model/repository retry fields; scheduler stale-`RUNNING` recovery. |
| **3. Temporary API error** | Explicitly caught network/timeout failures return retryable failure. Scheduler records `FAILED` with 15/30/60/120-minute backoff. Attempt 5 becomes `EXHAUSTED`. Some malformed/non-network errors are classified non-retryable. | Retry temporary errors with bounded consumption and an eventual recovery path. **Recovery: yes up to attempt 5; not thereafter.** | `FinalMatchSyncService.sync_final_match`; repository `mark_failure`. |
| **4. `EXHAUSTED`, then provider becomes `PEN`** | `EXHAUSTED` is absent from due queries; `ensure_pending` does not reset it; candidate creation filters it out. | Reconsider newly terminal evidence or provide controlled recovery. **Recovery through current scheduler/repair: no.** | Repository `ensure_pending`/`get_due_match_ids`; scheduler candidate filters; **CRITICAL gap**. |
| **5. `PEN`, but validation rejects final data** | Invalid/missing elapsed or goals return `INVALID_FINAL_MATCH_STATE`, non-retryable; scheduler records `EXHAUSTED`. No penalty score fields are required. | Recoverable validation/provider incompleteness should be retried or investigated. **Automated recovery: no after exhaustion.** | `_validated_state`; scheduler `mark_failure`; **HIGH/CRITICAL gap**. |
| **6. Match remains `P` for more than 24 hours** | Under continuous scheduler operation, repeated `WAITING` responses exhaust after five calls (nominal waits total 60 minutes, excluding execution delays). Subsequent all-live sweeps see the row but skip it. If work was delayed by an outage before exhaustion, persisted pending work can resume after recovery. | Continue low-frequency checks or bounded historical recovery. **Recovery after the five `P` attempts: no.** | Waiting delay constants and `mark_waiting`; candidate skip. |
| **7. Two workers attempt same match** | The active scheduled route is serialized by process-local `max_instances`, a DB session advisory lock across scheduler instances, row locking, and a global resource lock. | Avoid duplicate concurrent finalization. **Current scheduled path: controlled.** Direct legacy helper uses a different lock namespace and is not mutually exclusive by lock identity, but normal wrappers disable it. | Scheduler `start`, `_acquire_advisory_lock`, `_finalize_match_sync_candidates`; `resource_lock.py`. |
| **8. Partial write then failure** | Final match update occurs before event refresh. A returned event failure is marked retryable; scheduler commits the state and shared transaction, so final match values may persist with `FAILED`, and a later attempt can complete event sync. A database exception can roll back or leave `RUNNING` for stale recovery. | No lost finalization; repeat safely. **Recovery: generally yes before exhaustion; partial canonical final values can be visible before `SUCCESS`.** | `FinalMatchSyncService.sync_final_match`; scheduler's transaction and commit branches; event replacement repository. Runtime transaction outcome not verified. |

## 12. Test Coverage Gaps

### Existing focused coverage

`tests/test_final_match_sync_phase3.py` covers:

- `P` as waiting/non-terminal and `FT`/`AET`/`PEN` as accepted terminal statuses.
- Temporary timeout and event-sync failures.
- Retry backoff and attempt-five exhaustion for provider failure and waiting results.
- Persisted retry scheduling and stale-`RUNNING` restart recovery.
- Successful-finalization idempotency.
- A concurrent scheduler call test that checks duplicate provider calls under mocked locking.

`tests/test_final_live_sync.py` covers fresh terminal fetches, rejection of a non-terminal refresh, persistence failure, provider failure, lock contention, and terminal handoff ordering.

### Missing or incomplete coverage

- No full repeated-response lifecycle proving `P` → `P` ×5 → `PEN` can complete.
- No recovery-after-`EXHAUSTED` test; current tests explicitly assert that the sixth attempt is skipped.
- No test that re-discovers a historical `P` match after 24 hours or proves the limits of Daily Repair.
- No validation matrix for missing, boolean, negative, or wrong-type elapsed/goals, nor expected recovery policy.
- No test for `PEN` with absent penalty-score fields (and no test should presume those fields are required).
- No test that a stale live-feed `P` cannot regress a previously finalized match.
- No real PostgreSQL concurrency/transaction test; scheduler concurrency test mocks the resource lock and uses an in-memory fake record.
- No runtime application-restart/database-persistence test.

### Audit test execution

The focused command `python -m pytest -q tests/test_final_live_sync.py tests/test_final_match_sync_phase3.py` produced **33 passed, 16 failed**. A representative failure occurs because a test passes `object()` as the DB session while the current resource-lock implementation requires `db.execute()`; the lock acquisition fails before the tested operation. This confirms the focused test set is not fully passing in this checkout, but does not by itself establish whether the affected production path fails with a real `AsyncSession`. No tests were modified.

## 13. Design Gap Matrix

| Area | Current Implementation | Target Requirement | Gap | Severity | Evidence |
|---|---|---|---|---|---|
| `P` / `PEN` classification | `P` is waiting/live; `PEN`, `AET`, `FT` are terminal. | Never infer terminal from `P`; require provider confirmation. | None in status classification. | **NO GAP** | `final_match_sync_service.py` lines 13–14, 145–167. |
| Fresh terminal confirmation | Exact fixture response must return `FT`/`AET`/`PEN`. | Confirmed terminal provider status. | None in active finalizer. | **NO GAP** | `final_match_sync_service.py` lines 145–167. |
| Retry `P` and temporary errors | Persisted capped waits, five attempts. | Controlled retries without permanent abandonment at a short cap. | Fifth wait/error becomes terminal `EXHAUSTED`. | **CRITICAL** | `match_finalization_repository.py` lines 10–25, 115–160. |
| `EXHAUSTED` recovery | Excluded from due rows and candidate lists; no reset path. | Recover eligible exhausted matches. | Match can remain permanently unattended. | **CRITICAL** | `get_due_match_ids`, `ensure_pending`; scheduler lines 664, 695; fixture sync lines 703, 1258, 1279. |
| Persistent pending/restart | Retry state and claims live in the database; stale `RUNNING` recovered. | Pending survives restarts. | Satisfied for non-exhausted records; no recovery after exhaustion. | **NO GAP** (with exhausted caveat above) | `match_finalization.py`; repository; scheduler lines 661–683. |
| Historical recovery | Daily Repair queries yesterday/today; all local live statuses are separately swept. | Bounded historical recovery. | No dedicated bounded old-match recovery; older local `P` is only covered by ongoing live sweep until exhausted. | **MEDIUM** | `scheduler.py` lines 1039–1104; `match_repository.py` `get_live_matches`; fixture sync lines 1276–1289. |
| Final-data validation | Terminal status, elapsed and goals validated; penalty scores are not read. Invalid data is non-retryable. | Observable and recoverable validation failures. | Invalid terminal payload immediately exhausts; canonical penalty score fields are not written here. | **HIGH** | `final_match_sync_service.py` lines 145–167, 250–268; model `match.py`. |
| Active `final_live_sync` wiring | Direct helper is behind `handle_terminal_transition`; normal sync wrappers disable transition mode. Scheduler uses another finalizer. | Reliable scheduled finalization in existing architecture. | Naming/flow mismatch; no dedicated scheduled retry state for the helper itself. | **HIGH** | `fixture_sync_service.py` lines 288–430, 1098–1127; `scheduler.py` lines 716–720. |
| Scheduler concurrency | `max_instances=1`, DB advisory lock, row lock, global resource lock. | Prevent duplicate concurrent processing. | No gap found for active scheduled path; direct legacy path uses a different lock namespace if enabled. | **NO GAP** (active scheduler) | `scheduler.py` lines 503–514, 641–830; `resource_lock.py`. |
| Idempotency | Success state skipped; match update is an update; event rows replaced. | Repeated finalization safe. | Finalizer repeats safely, but independent live sync may overwrite terminal status with stale `P`. | **CRITICAL** | `match_repository.py` lines 21–49; `fixture_sync_service.py` lines 1235–1289. |
| Cache behavior | Invalidate after confirmed finalization success. | Keep cache consistent. | Failure leaves cached final data stale until later success/other invalidation. | **MEDIUM** | `scheduler.py` `_invalidate_final_match_caches` and success branch, lines 607–639, 821–826. |
| Tests | Unit tests cover isolated status/retry states and mocked scheduler concurrency. | Verify end-to-end transition/recovery and real persistence. | No repeated P-to-PEN after retries, exhausted recovery, historical/runtime tests; focused suite has failures. | **MEDIUM** | `tests/test_final_match_sync_phase3.py`; `tests/test_final_live_sync.py`; audit run above. |

## 14. Root Cause Findings

1. **Retry exhaustion is terminal in practice.** The fifth wait/failure removes the row from the due query, and no existing code resets it. This directly permits a later `PEN` to be missed.
2. **Candidate registration is intentionally gated on finalization state.** `EXHAUSTED` is treated like `SUCCESS` for candidate creation, even though it represents an unfinished match.
3. **The canonical status write has no monotonicity guard.** Normal live sync can write a stale active status over a terminal match, independently of finalization state.
4. **Two flows share similar naming but different guarantees.** `final_live_sync` is a one-shot terminal refresh without durable retry state; the scheduled durable flow is `FinalMatchSyncService`.
5. **Some validation failures are classified as permanently non-retryable.** An incomplete but otherwise valid `PEN` provider response can be exhausted before a corrected response is available.

## 15. Minimal Recommended Fix Scope

No redesign or new service is needed. Within the existing scheduler/service/repository architecture:

1. Keep `P` explicitly non-terminal, but allow a persisted, controlled long-interval retry/recovery after the current short retry budget. Ensure an eligible `EXHAUSTED` row can be reconsidered, particularly when the provider later reports a terminal status.
2. Do not silently treat an exhausted unfinished match as successfully handled. Preserve its error reason and expose its recovery/operational state.
3. Classify incomplete terminal payload validation so transient missing values can retry or be investigated instead of immediately becoming unrecoverable.
4. Prevent ordinary live-feed synchronization from regressing a canonical terminal status on a stale non-terminal response.
5. Add focused tests for the full P-to-PEN lifecycle past the current attempt threshold, EXHAUSTED reactivation, validation recovery, historical P discovery, and stale-status regression. Rework the affected lock-dependent tests to use a DB fake that implements the resource-lock contract or to mock the lock explicitly.

These are targeted changes to existing components; no replacement synchronization architecture is recommended.

## 16. Final Audit Verdict

**DESIGN GAP FOUND — Existing design needs targeted improvements.**

1. **Can current `final_live_sync` reliably complete a match transitioning from `P` to `PEN`?**  
   **Not reliably.** The active durable worker correctly waits for `P` and requires provider-confirmed `PEN`, but stops after five attempts. The specifically named `FixtureSyncService.final_live_sync` helper is not the scheduled worker in the inspected normal path.
2. **Can a match become permanently unattended after `EXHAUSTED`?**  
   **Yes.** It is absent from due selection, candidate registration skips it, and no recovery reset was found.
3. **Can historical matches stuck in `P` be recovered?**  
   **Conditionally.** Non-exhausted local `P` rows are included in the live-status sweep without a date cutoff. Daily Repair itself only checks yesterday and today. Exhausted historical rows cannot be recovered by the current mechanisms.
4. **Does the current design survive application restarts?**  
   **Yes for persisted `PENDING`/`FAILED` and stale `RUNNING` work**, because attempts and retry times are stored and stale claims are recovered. This does not rescue `EXHAUSTED`.
5. **What is the smallest required change to make finalization reliable?**  
   **Make recoverable `P`/temporary-failure work continue on a persisted controlled schedule beyond the fifth attempt, and provide an explicit reactivation path for eligible `EXHAUSTED` rows.** Also guard the normal live-state update against terminal-to-nonterminal regression to protect already finalized data.

**Runtime verification:** Not performed. Provider behavior, deployed scheduler state, actual database migration/schema state, and PostgreSQL transaction/concurrency behavior remain **NOT VERIFIED**.
