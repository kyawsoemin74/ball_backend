# FINAL_LIVE_SYNC Final Design Compatibility Audit

**Audit type:** Read-only, pre-implementation design validation  
**Evidence basis:** Checked-in source, migration, Kubernetes manifests, and tests  
**Runtime verification:** Not performed  
**Verdict:** **READY AFTER DESIGN ADJUSTMENT**

## 1. Executive Summary

The agreed finalization design fits the existing Provider → Service (Read) → SyncService (Write Orchestration) → Repository (Persistence) → Database architecture. The core persistent state already exists in `match_finalization`; it stores state, attempt count, retry time, last attempt, error details, and completion time. **A new table or migration is not required** if existing states are used semantically:

- `PENDING`: eligible/pending and retry-wait work;
- `RUNNING`: claimed work;
- `FAILED`: recoverable failures with `next_retry_at`;
- `SUCCESS`: finalized;
- `EXHAUSTED`: permanent/investigation state that must have an explicit reactivation path.

The current implementation does not yet satisfy the agreed behavior. In particular, five attempts are terminal in practice: both `P` responses and retryable errors can become `EXHAUSTED`; the due query and candidate selection skip that state; and no recovery path reactivates it. The canonical match write path also has no protection against stale live data overwriting a terminal status.

The design is therefore **compatible with the existing architecture and schema, but needs policy details resolved before implementation**: the extended retry cadence and provider-call budget; the meaning of “bounded” historical recovery; the reactivation rules for old `EXHAUSTED` rows; and whether `RECOVERABLE_FAILURE` is a conceptual category or a new persisted state. The recommendation is to keep it a category represented by `FAILED` plus existing error fields, avoiding a schema change.

No source, model, migration, scheduler, configuration, database record, or service was changed. Actual deployed database metadata, provider quota, runtime scheduler state, and production transaction behavior were **NOT VERIFIED**.

## 2. Existing Finalization Architecture

### Active scheduled path

The current scheduled final-match path is:

1. `FixtureProvider.get_live_fixtures` reads `/fixtures?live=all`; `get_fixtures_by_ids` reads `/fixtures?ids=...` (`app/providers/fixture_provider.py`, lines 49–53).
2. `FixtureSyncService.sync_live_matches` applies live-feed status/score updates and identifies terminal transitions and locally live matches missing from the provider live list (`app/services/fixture_sync_service.py`, `sync_live_matches`, lines 1160–1290).
3. `LiveUpdateScheduler._sync_live_matches_job` runs every 60 seconds. It gets due finalization rows, runs the durable finalizer, then processes the normal live feed and any newly discovered candidates (`app/services/scheduler.py`, `start`, lines 503–514; `_sync_live_matches_job`, lines 879–953).
4. `_finalize_match_sync_candidates` claims a row as `RUNNING`, commits the claim, and delegates to `football_service.sync_final_match_state` (`scheduler.py`, lines 641–830).
5. `FootballService.sync_final_match_state` delegates to `FinalMatchSyncService.sync_final_match` (`app/services/football.py`, lines 140–145 and 183–184).
6. `FinalMatchSyncService` fetches the exact provider fixture, accepts only `FT`/`AET`/`PEN`, validates status/elapsed/goals, persists through `MatchRepository`, refreshes events, and marks the state `SUCCESS` (`app/services/final_match_sync_service.py`, lines 44–268).
7. Scheduler transaction handling commits results and invalidates caches after success.

### Separately named `final_live_sync`

`FixtureSyncService.final_live_sync` is a separate one-shot helper. It fetches by fixture ID, requires a fresh terminal status, persists through fixture synchronization, and returns success/failure (`app/services/fixture_sync_service.py`, lines 288–370). It calls `run_with_resource_lock` under `fixture:<provider_fixture_id>`.

The helper is called by `handle_terminal_transition` (lines 379–430). However, the normal full-season, daily, and live sync wrappers set `_allow_terminal_transition` to `False` while processing (`fixture_sync_service.py`, approximately lines 1098–1127); no normal source caller enabling it was found. The scheduled durable flow uses `FinalMatchSyncService`, not this helper. The design should therefore extend the active durable flow rather than create a second retry mechanism around `final_live_sync`.

### Related final data

The active finalizer refreshes match state and events. The legacy transition handler also requests statistics sync and creates final-lineup work. Those responsibilities should remain with their current services/jobs; enabling both finalization routes without coordinating them could duplicate work.

## 3. Current Execution Flow and State Semantics

| Design concept | Existing representation | Compatibility |
|---|---|---|
| `FINALIZATION_PENDING` | `PENDING` | Direct semantic mapping. |
| `FINALIZATION_RUNNING` | `RUNNING` | Direct semantic mapping; claimed before provider work. |
| `RETRY_WAIT` | `PENDING` or `FAILED` plus `next_retry_at` | Existing scheduler skips until the stored time. |
| `RECOVERABLE_FAILURE` | `FAILED` plus `last_error_category`, `last_error_message`, and `next_retry_at` | Can be represented without adding a database state. |
| `FINALIZED` | `SUCCESS` plus `completed_at` | Direct semantic mapping. |
| Permanent/investigation failure | `EXHAUSTED` plus error fields | Existing persisted state; current code lacks the required recovery/alert behavior. |

`P` is classified as waiting, not terminal; `PEN`, `FT`, and `AET` are terminal (`app/services/final_match_sync_service.py`, lines 13–14). The finalizer does not derive terminality from elapsed time or convert `P` to `PEN`.

The repository currently caps attempts at five. `P` waiting intervals are 5, 10, 15, and 30 minutes; retryable failures use 15, 30, 60, and 120 minutes. On the fifth response/failure, the row can become `EXHAUSTED`. `get_due_match_ids` selects due `PENDING`/`FAILED` and stale `RUNNING`, not `EXHAUSTED` (`app/repositories/match_finalization_repository.py`, lines 10–25, 67–96, 115–160).

## 4. Final Design Compatibility Matrix

| Design requirement | Existing component | Compatibility | Required change | Evidence |
|---|---|---|---|---|
| Persist pending finalization across restart | `MatchFinalization`, repository, scheduler | **FULLY COMPATIBLE** | Retain use of persistent row; keep `next_retry_at` durable. | [`app/models/match_finalization.py`](../app/models/match_finalization.py#L7); [`app/repositories/match_finalization_repository.py`](../app/repositories/match_finalization_repository.py#L40); [`app/services/scheduler.py`](../app/services/scheduler.py#L661). |
| `P` remains non-terminal; `PEN`/`FT`/`AET` terminal | `FinalMatchSyncService` | **FULLY COMPATIBLE** | Preserve existing status sets; add regression tests. | [`app/services/final_match_sync_service.py`](../app/services/final_match_sync_service.py#L13), lines 145–167. |
| Retry `P` with controlled intervals | Repository and 60-second scheduler | **PARTIALLY COMPATIBLE** | Replace terminal attempt cap for recoverable `P` with persisted slower retries; preserve budget/cadence limits. | [`app/repositories/match_finalization_repository.py`](../app/repositories/match_finalization_repository.py#L17), lines 140–160. |
| Retry temporary API/network errors | FinalMatchSyncService and repository | **PARTIALLY COMPATIBLE** | Keep error classes/categories distinct; continue on capped long backoff instead of permanent abandonment. | [`app/services/final_match_sync_service.py`](../app/services/final_match_sync_service.py#L90), [`app/repositories/match_finalization_repository.py`](../app/repositories/match_finalization_repository.py#L115). |
| Recover incomplete terminal validation | FinalMatchSyncService | **PARTIALLY COMPATIBLE** | Categorize incomplete/transient provider values as recoverable; reserve permanent/investigation treatment for actual permanent faults. | [`app/services/final_match_sync_service.py`](../app/services/final_match_sync_service.py#L167), lines 250–268. |
| Recover existing `EXHAUSTED` rows | Repository, scheduler, fixture candidate discovery | **NOT COMPATIBLE** | Add an explicit, guarded reactivation path using the existing row and retry timestamp; do not treat `EXHAUSTED` as equivalent to `SUCCESS`. | [`app/services/scheduler.py`](../app/services/scheduler.py#L664); [`app/services/fixture_sync_service.py`](../app/services/fixture_sync_service.py#L1258), lines 1276–1289. |
| Recover work after process restart | Scheduler stale-`RUNNING` recovery | **FULLY COMPATIBLE** for non-exhausted rows | Preserve attempt count and retry time; test against a real DB/session lifecycle. | [`app/services/scheduler.py`](../app/services/scheduler.py#L661), [`app/repositories/match_finalization_repository.py`](../app/repositories/match_finalization_repository.py#L67). |
| Recover matches missed during downtime | Persistent due rows and live-status candidate sweep | **PARTIALLY COMPATIBLE** | Re-enqueue/recognize older unresolved rows and ensure recovery is not limited to new terminal transitions. | [`app/services/fixture_sync_service.py`](../app/services/fixture_sync_service.py#L1276); [`app/repositories/match_repository.py`](../app/repositories/match_repository.py#L100). |
| Bounded historical recovery | Daily Repair plus live-status sweep | **PARTIALLY COMPATIBLE** | Extend the existing due-work path with a bounded per-cycle budget and explicit historical eligibility; Daily Repair alone only fetches two dates. | [`app/services/scheduler.py`](../app/services/scheduler.py#L1039), lines 1043–1055; [`app/repositories/match_finalization_repository.py`](../app/repositories/match_finalization_repository.py#L67). |
| No terminal-to-nonterminal regression | Live sync and `MatchRepository.update_live_state` | **NOT COMPATIBLE** | Add an atomic terminal-status guard at persistence; service should classify/report skipped stale updates. | [`app/services/fixture_sync_service.py`](../app/services/fixture_sync_service.py#L1235); [`app/repositories/match_repository.py`](../app/repositories/match_repository.py#L21). |
| Idempotent match/event finalization | FinalMatchSyncService, repositories, success state | **PARTIALLY COMPATIBLE** | Preserve success short-circuit and event replacement; verify rollback/commit behavior with integration tests. | [`app/services/final_match_sync_service.py`](../app/services/final_match_sync_service.py#L52), lines 176–248; [`app/repositories/event_repository.py`](../app/repositories/event_repository.py#L18). |
| Concurrent-worker protection | Scheduler advisory lock, row lock, resource lock | **FULLY COMPATIBLE** for active scheduled path | Reuse existing locks; coordinate rather than enabling a separate helper flow. | [`app/services/scheduler.py`](../app/services/scheduler.py#L1582), lines 641–680; [`app/services/resource_lock.py`](../app/services/resource_lock.py#L11). |
| No duplicate finalization system | Existing finalizer and scheduled due query | **PARTIALLY COMPATIBLE** | Extend current `MatchFinalization` lifecycle; do not add another worker/table or schedule a parallel `final_live_sync` retry loop. | [`app/services/scheduler.py`](../app/services/scheduler.py#L889); [`app/services/football.py`](../app/services/football.py#L183). |
| Cache consistency after finalization | Scheduler success path | **PARTIALLY COMPATIBLE** | Keep post-commit invalidation; additionally ensure an external/stale status regression cannot leave terminal cache state inconsistent. | [`app/services/scheduler.py`](../app/services/scheduler.py#L607), lines 821–826. |
| Actual deployed schema/runtime correctness | Database and production services | **NOT VERIFIED** | Read-only deployment metadata/runtime verification before rollout. | No database metadata query or provider/runtime execution was performed. |

## 5. Database Compatibility

### Existing checked-in model and migration

`MatchFinalization` already includes:

- one row per local match (`match_id` primary key);
- persistent `state` and `attempt_count`;
- `next_retry_at` and `last_attempted_at`;
- `completed_at`;
- `last_error_category` and `last_error_message`;
- timestamps and a `(state, next_retry_at)` index.

The migration `alembic/versions/20261005_create_match_finalization.py` creates these columns and constraints. The existing fields can support pending work, retry waits, recoverable failures, investigation errors, and completion. The existing `Match` model stores terminal status and home/away scores; penalty shootout scores are not fields on canonical `Match`, and the active finalizer does not require penalty-score fields.

### Migration decision

**A new table is not necessary. A migration is not necessary** for the agreed behavior if conceptual `RECOVERABLE_FAILURE` maps to existing `FAILED` and the existing state set remains unchanged.

If implementation insists on persisting a distinct `RECOVERABLE_FAILURE` state, the current model and migration check constraint allow only `PENDING`, `RUNNING`, `SUCCESS`, `FAILED`, and `EXHAUSTED`; that choice would require coordinated model and migration changes. It is not recommended because existing `FAILED` plus error category/message and `next_retry_at` already provide the distinction.

### Verification boundary

**CONFIRMED:** The checked-in model and migration contain the fields above.  
**NOT VERIFIED:** Whether the migration is applied in the deployed database, whether its live index/constraint definitions match the repository, and whether current records have valid states. No DB metadata or production queries were made.

## 6. Retry Policy Assessment

### Current implementation

- `P`: waits 5, 10, 15, and 30 minutes; fifth non-terminal result becomes `EXHAUSTED`.
- Retryable API/network/event failures: waits 15, 30, 60, and 120 minutes; fifth retryable failure becomes `EXHAUSTED`.
- Some malformed provider responses and invalid final values are immediately non-retryable.
- `EXHAUSTED` receives no `next_retry_at` and is not queried again.

This is a good starting shape for differentiated retries but does not meet the “finite limit must not permanently abandon a match” requirement.

### Recommended policy (proposal only)

Retain the existing short initial schedule because it gives prompt confirmation during the expected short transition. Then slow down rather than exhaust:

| Outcome | Proposed schedule | Rationale and API impact |
|---|---|---|
| Provider returns `P` | Keep current immediate attempt and 5/10/15/30-minute waits. After the fifth `P`, retry no more often than every 6 hours for the next day; thereafter at most once per 24 hours while still unresolved. | Captures near-term status changes, then caps a single unresolved match at four extra lookups/day. Each retry is one exact fixture-ID provider request. |
| Temporary API/network error | Keep 15/30/60/120-minute backoff, add jitter, then cap at one retry every 6 hours while the error remains recoverable. | Separates request failure from a valid active response and avoids synchronized retry bursts. |
| Incomplete terminal response | Persist a distinct validation error category and retry at a slower cadence (for example 30 minutes, then 2 hours, then 6 hours), unless the payload indicates a permanent data/configuration defect. | Allows eventual provider correction without polling as frequently as an in-progress match. |
| Permanent identity/configuration error | Do not poll repeatedly. Retain `EXHAUSTED`/investigation state, log/alert it, and permit explicit reactivation after the identity/configuration is corrected. | Avoids wasting provider credits on an error a provider request cannot fix. |

The intervals are proposals, not provider-quota facts. The six-hour extended interval implies at most four follow-up fixture-ID requests per unresolved match per day, before moving to daily checks. The provider's actual credit rules, current outstanding-match count, and operational response objective were not available, so the **global** request budget must be set from quota/traffic evidence before coding.

The existing due query limits one due-row query to 100 IDs per scheduler cycle, but this is not a sufficient global API budget by itself: live sync can also return candidates from the full local live-match sweep, and no global provider-request limiter was verified. The implementation should apply one per-cycle finalization budget across both candidate sources, avoid processing the same match twice in a cycle, and defer overflow durably using `next_retry_at`.

**Proposed historical recovery window:** Do not impose a match-age cutoff on unfinished rows. Keep `P` rows eligible indefinitely, moving from the initial checks to six-hour polling during the first day and daily polling thereafter. Bound API use by the global per-cycle/per-day request budget, not by dropping older matches. This is the only interpretation that both recovers arbitrarily late provider transitions and avoids a new permanent-abandonment cutoff. If operations requires a finite lookback, that is a design adjustment requiring an explicit manual recovery/escalation path for older rows.

### `EXHAUSTED` semantics to resolve

The design should distinguish:

- **Recoverable exhaustion of the fast retry phase:** transition to slower persisted `FAILED`/`PENDING`, not terminal abandonment.
- **Permanent/investigation condition:** retain `EXHAUSTED` and expose a guarded reactivation path when the underlying issue is corrected or fresh terminal evidence arrives.

Do not reset attempt count or retry all old rows on every live-feed cycle. Reactivation must be conditional and rate-limited.

## 7. Recovery Compatibility

| Recovery case | Current support | Compatible extension |
|---|---|---|
| Pending work after restart | Persisted due rows are loaded on the next 60-second job; stale `RUNNING` rows older than five minutes are recovered. | Reuse existing row and scheduler. |
| Previously `EXHAUSTED` | Not selected by due query; candidate code skips it; no reset method found. | Extend the current repository/scheduler lifecycle to rearm eligible rows using persisted retry time and category. |
| Matches missed during downtime | Existing local live statuses are swept; pending rows remain durable. A local status already changed to terminal may not be considered live. | Historical candidate query must include unfinished finalization rows, not just current live statuses or newly observed transitions. |
| Historical `P` matches | `get_live_matches` has no date cutoff, so old rows still marked live can be rediscovered. Daily Repair itself is yesterday/today only. | Use the existing finalization due queue for all unfinished rows, with bounded batch and call-rate controls. |
| Provider later changes `P` to `PEN` | Works only while a pending/failed row is due. An exhausted record is skipped. | Rearm recoverable exhausted rows; on fresh terminal evidence, schedule a final exact-fixture validation without bypassing provider confirmation. |

### Bounded historical mechanism

The agreed design does not specify whether “bounded” means a finite lookback age or bounded work per run. A finite age cutoff conflicts with the requirement not to permanently abandon late provider corrections. Recommended interpretation:

- keep all unfinished records eligible until finalized or explicitly classified as permanent/investigation;
- bound **work**, not eligibility age: a quota-derived maximum number of fixture-ID requests per cycle/day, stable due ordering, and at most one request per match per cooldown;
- for long-unresolved records, reduce polling to daily and surface the record for investigation.

If product/operations instead requires a finite historical lookback, specify the age cutoff and manual escalation path before implementation; otherwise older `P` matches will again be permanently unattended.

This reuses `sync_live_matches` and `MatchFinalizationRepository`; it does not require a separate worker or queue. Daily Repair can remain focused on yesterday/today.

## 8. Status Regression Protection

### Confirmed issue

`FixtureSyncService.sync_live_matches` reads the existing status and calls `MatchRepository.update_live_state` with the provider response. The repository updates status, elapsed, and scores with a `WHERE local_match_id = ...` condition only. There is no condition preventing an existing `PEN`, `FT`, or `AET` row from being overwritten by a stale `P`/live status (`app/services/fixture_sync_service.py`, lines 1235–1251; `app/repositories/match_repository.py`, lines 21–49).

The finalizer itself does not write `P`: it accepts only a confirmed terminal status. The regression path is the ordinary live-feed update.

### Recommended protection location

Use the repository write boundary for the atomic invariant: a non-terminal live-state update must not overwrite an existing terminal status. The SyncService should interpret a rejected/stale update as a skipped update and record useful evidence. Putting the invariant only in the service would leave a race between reading the old status and writing the provider update.

Do not convert one terminal status into another based on a guessed mapping. Keep `PEN`, `FT`, and `AET` distinct and let only an authoritative terminal provider payload update a terminal row.

## 9. Scheduler and Concurrency Assessment

- The active job runs every 60 seconds and uses APScheduler `max_instances=1`.
- A PostgreSQL session-level advisory lock on a pinned connection coordinates multiple scheduler processes.
- Finalization records are read with `FOR UPDATE`; `RUNNING` is committed before the provider call.
- The scheduled finalizer and live sync use the `live_sync:global` transaction resource lock; event refresh uses an event-specific lock.
- Kubernetes manifests specify one worker replica and HPA max replicas 1. These are checked-in deployment settings, **not proof of deployed replica count**.
- The one-shot `final_live_sync` helper uses `fixture:<provider_fixture_id>`, a different lock namespace from the durable scheduler's global lock. Do not independently reactivate both flows; retain one authoritative finalization route.

**Compatibility:** Existing locks are sufficient to extend the active scheduled flow. The proposed recovery path should use the existing scheduler job and row lifecycle; it should not add overlapping periodic jobs.

**NOT VERIFIED:** Production pod count, PostgreSQL advisory lock behavior in the live environment, and transaction outcomes were not tested.

## 10. API Credit and Production Safety Assessment

### Source-confirmed call shape

- Normal live sync calls the provider's `live=all` endpoint when the scheduler gate finds eligible matches.
- Each finalization attempt calls the exact fixture-ID endpoint once.
- A confirmed terminal finalization also refreshes events.
- The due-row query default limit is 100 per cycle; candidate processing from live sync is not globally capped in the inspected code.
- The scheduler interval is 60 seconds and is serialized by scheduler/advisory locks.

### Assessment

Controlled per-match backoff is feasible using `next_retry_at`; the scheduler already prevents overlapping job instances and multiple scheduler instances. However, **production API-credit safety is NOT VERIFIED** because quota, active-match count, exhausted backlog, API response cost, and deployed scheduler state are unknown. A 100-row due query is a batch ceiling, not a verified safe API-credit budget; live-discovered candidates need to share the same per-cycle cap.

Before rollout, define an operator-approved global request budget and metric/logging for due count, delayed count, recovered exhausted count, provider request count, and oldest unfinished age. Maintain the existing post-commit cache invalidation; verify it with integration tests after changes.

## 11. Existing Components That Can Be Reused

| Component | Reuse |
|---|---|
| `FixtureProvider` | Exact-ID terminal verification and live fixture reads. |
| `FinalMatchSyncService` | Status gate, final validation, canonical update, event refresh, success idempotency. |
| `MatchFinalization` | Durable state, attempts, timestamps, failure reason, completion. |
| `MatchFinalizationRepository` | Due query, state transitions, retry timestamps, row lock. |
| `LiveUpdateScheduler._sync_live_matches_job` | Existing trigger, candidate processing, advisory lock, transaction and cache lifecycle. |
| `FixtureSyncService.sync_live_matches` | Candidate discovery and live-feed integration, after adding regression protection and exhaustion eligibility policy. |
| `MatchRepository` | Canonical status/score persistence; add atomic terminal-state protection here. |
| `run_with_resource_lock` and scheduler advisory locks | Existing concurrency coordination. |
| `EventSyncService` / `EventRepository` | Existing event refresh and replacement behavior. |

No new synchronization architecture, queue, worker, or table is justified by the source audit.

## 12. Required Minimal Changes

### Application files expected to change

1. [`app/repositories/match_finalization_repository.py`](../app/repositories/match_finalization_repository.py) — allow persistent long-tail retry; avoid terminalizing recoverable `P` and temporary failures after the fast-attempt cap; define guarded reactivation behavior and due selection.
2. [`app/services/scheduler.py`](../app/services/scheduler.py) — apply one shared per-cycle request budget; include eligible recovery rows; surface aged/exhausted states; preserve existing job and locks.
3. [`app/services/fixture_sync_service.py`](../app/services/fixture_sync_service.py) — candidate handling must not treat all `EXHAUSTED` rows as successful; avoid repeated reactivation on each live cycle.
4. [`app/repositories/match_repository.py`](../app/repositories/match_repository.py) — atomically prevent non-terminal status updates from overwriting terminal state.
5. [`app/services/final_match_sync_service.py`](../app/services/final_match_sync_service.py) — distinguish recoverable incomplete terminal payloads from permanent data/identity errors and maintain explicit provider-confirmed status handling.

### Test files expected to change

- `tests/test_final_match_sync_phase3.py`: retry phases, recovery after exhaustion, validation classifications, old `P` discovery, and idempotent terminal persistence.
- `tests/test_final_live_sync.py`: confirm the one-shot helper remains distinct from the active durable path or test any intentional coordination.
- `tests/test_scheduler_runtime.py` and/or `tests/test_live_sync_phase6.py`: global cycle budget, terminal regression rejection, and scheduler lock/commit behavior.

These are expected implementation/test files only; **no file was changed during this audit**.

## 13. Unnecessary Changes That Must Be Avoided

- Do not add a new finalization table or queue.
- Do not add a new worker or an overlapping scheduler job.
- Do not add `RECOVERABLE_FAILURE` as a new persisted state unless there is a concrete query/operational need; `FAILED` plus error category and retry time is sufficient.
- Do not manually map `P` to `PEN`, infer terminal status from elapsed time, or collapse `PEN`/`FT`/`AET`.
- Do not require penalty shootout score fields for canonical finalization unless product requirements explicitly add them; current canonical `Match` stores a home/away score pair, and provider penalty-score availability was not runtime verified.
- Do not re-enable the legacy transition helper alongside the active durable finalizer without consolidating state ownership and locks.
- Do not change unrelated statistics, lineup, odds, or frozen architecture modules.

## 14. Implementation Dependency Order

1. **Agree policy semantics:** distinguish recoverable errors from permanent investigation; define the “bounded” historical requirement as bounded work/request rate or specify an explicit lookback plus escalation.
2. **Set API budget from operational facts:** provider quota, request-credit rules, active/unfinished match volume, and desired completion latency. The source alone cannot choose a safe global budget.
3. **Implement repository state transitions and due ordering** using the current table and fields; define reactivation of old `EXHAUSTED` rows.
4. **Implement scheduler-level candidate deduplication and shared request cap** while retaining current job interval and locks.
5. **Add repository-level terminal status guard** so stale live data cannot regress a finalized Match.
6. **Adjust validation outcome classification** so temporary/incomplete provider fields are recoverable and permanent identity/data faults remain observable.
7. **Add focused unit tests**, then DB-backed integration tests for transaction rollback, worker restart, concurrent attempts, recovery, status monotonicity, and cache invalidation.
8. **Before production rollout, verify** the deployed migration state, worker replica count, provider quota, and runtime metrics. No runtime safety claim can be made from this audit.

## 15. Final Verdict

### Implementation-readiness verdict

**READY AFTER DESIGN ADJUSTMENT**

The architecture and checked-in schema can support the design. Before coding, resolve the retry/recovery semantics and API budget, and define “bounded historical recovery” so it does not silently reintroduce permanent abandonment. The terminal-status regression invariant must be included in the implementation scope.

### Explicit answers

1. **Can the agreed Final Design fit the existing Frozen Architecture?**  
   **Yes.** It can be implemented within the existing Provider, Service, SyncService, Repository, and database layers.
2. **Can existing services and tables be reused?**  
   **Yes.** Reuse `FinalMatchSyncService`, `MatchFinalization`, its repository, the existing live scheduler, fixture sync, and match repository.
3. **Is a new database table actually necessary?**  
   **No.** Existing columns support the lifecycle if conceptual recoverable/investigation states map to `FAILED` and `EXHAUSTED` with existing error fields.
4. **What exact files need modification?**  
   Expected: `app/repositories/match_finalization_repository.py`, `app/services/scheduler.py`, `app/services/fixture_sync_service.py`, `app/repositories/match_repository.py`, `app/services/final_match_sync_service.py`, and the focused tests listed in Section 12.
5. **What exact database changes are required, if any?**  
   **None recommended.** A migration is needed only if implementation adds persisted states or fields; that is avoidable under the proposed mapping. Deployed schema state is **NOT VERIFIED**.
6. **What is the safest implementation order?**  
   Agree retry/recovery and quota semantics first; then update repository transitions, scheduler recovery/budget, atomic status protection, validation classification, and focused tests.
7. **What risks must be resolved before coding?**  
   Provider quota and request budget; definition of bounded historical recovery; eligibility/reactivation conditions for `EXHAUSTED`; validation retry versus investigation classification; and integration-test coverage for actual database transactions/locks.
8. **Can this design be implemented without replacing the existing finalization mechanism?**  
   **Yes.** Extend the current `MatchFinalization` state machine and scheduler flow; do not create a parallel finalization system.

**Production safety:** Source compatibility is established; deployed runtime safety, actual DB metadata, provider quota, live worker count, and real transaction/concurrency behavior remain **NOT VERIFIED**.
