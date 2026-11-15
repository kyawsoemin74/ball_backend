# Normal `live_sync` Target Design Audit

**Audit date:** 2026-11-13  
**Audit type:** Read-only architecture and design compatibility review  
**Final verdict:** **READY AFTER DESIGN ADJUSTMENT**  
**Runtime verification:** Not performed

## 1. Executive Summary

The proposed target is compatible with the existing Fover architecture. No replacement synchronization framework, new service, table, or migration is required. The design does, however, need to cover more than the normal live-feed update method:

1. **Terminal result regression is confirmed.** Normal Live Sync looks up a match by provider fixture identity and unconditionally writes the provider status, elapsed time, and two goals. A terminal local row is not excluded from that lookup or protected by the repository update.
2. **The fixture date/season upsert is another result writer.** Daily Fixture Sync, Daily Repair, and full-season sync use the same fixture upsert path, which unconditionally includes status, elapsed, and scores in its conflict update. Protecting only the normal live repository method would leave an alternate regression path.
3. **Application-level prechecks are not enough.** The current service reads local status before writing. A concurrent finalization can change the row after that read. The protection must be enforced atomically when the database write occurs.
4. **Existing locking is reusable but not consistently scoped.** Scheduled Live Sync and scheduled Finalization share the `LIVE_MATCH_SYNC_LOCK_KEY` session advisory lock and `live_sync:global` transaction lock. Daily Fixture Sync and Daily Repair use different session advisory keys and `fixture_query:global`; these do not coordinate with Live Sync. Existing transaction-scoped PostgreSQL resource locks can be reused with one shared per-match identity for match persistence.
5. **Status/score protection can be separated from metadata updates.** Normal Live Sync writes only status, elapsed, and home/away scores. The broader fixture upsert also refreshes fixture and identity metadata. Those sets must not be treated as one indivisible update.
6. **No source-level freshness ordering exists.** The Match model has `updated_at`, but the raw SQL live update and the fixture upsert do not set it; no provider version or update timestamp is used. A terminal guard can prevent terminal-to-nonterminal regression, but the code cannot reliably order two non-terminal snapshots or two terminal score corrections.

**Recommended minimal direction:** keep provider reads in the Provider/Service layers and orchestration in the existing SyncServices; make result-field protection an atomic persistence invariant in the existing Repository/persistence path; apply the same terminal-result policy to the fixture upsert; reuse `run_with_resource_lock` with the same per-local-match identity across live, finalization, daily/repair, and full-season result writes. Retain the existing scheduler-wide locks for job coalescing. A pre-read in `FixtureSyncService` may optimize or report the result, but must not be the only guard.

No tests, provider calls, sync jobs, API endpoints, or database queries were executed. No runtime database metadata or deployed-worker state was inspected.

## 2. Previous Audit Findings Verification

| Earlier finding | Current source verification | Impact |
|---|---|---|
| Terminal status regression | **CONFIRMED.** `FixtureSyncService.sync_live_matches` does not filter a looked-up row by current status before calling `update_live_state`. `MatchRepository.update_live_state` has only `WHERE local_match_id = :local_match_id`. | A stale `LIVE`, `P`, or other nonterminal payload can overwrite terminal status and result values. |
| Live Sync and Daily Repair use different lock scopes | **CONFIRMED.** Live Sync has session key `9342002` and transaction identity `live_sync:global`; repair has session key `9342004` and transaction identity `fixture_query:global`. | Those locks do not mutually exclude each other. |
| Finalization can be overwritten by later live-feed data | **CONFIRMED as a code-path possibility.** The live scheduler finalizes due work before its all-live sync and finalizes newly discovered candidates after the live update. The latter update can overwrite a finalizer's terminal result if the provider all-live payload is stale. | Finalization `SUCCESS` does not act as a guard on the `matches` row. |
| Execution order/persistence risk | **CONFIRMED.** The code commits the live update and then runs candidate finalization in the same scheduled cycle; the next cycle also runs due finalizations before live-feed writes. No shared freshness token is persisted. | Locks serialize some scheduled executions, but not all writer paths; order alone cannot guarantee newest data wins. |

Evidence: [`fixture_sync_service.py`](../app/services/fixture_sync_service.py#L1160), [`match_repository.py`](../app/repositories/match_repository.py#L21), [`scheduler.py`](../app/services/scheduler.py#L879), [`scheduler.py`](../app/services/scheduler.py#L654), [`scheduler.py`](../app/services/scheduler.py#L1039).

## 3. Current Normal Live Sync Execution Flow

### 3.1 Entry, schedule, and orchestration

1. The dedicated [`worker.py`](../worker.py#L16) process starts the scheduler through [`scheduler_service.py`](../scheduler_service.py#L14). The worker is separate from the API process in the checked local deployment configuration.
2. [`LiveUpdateScheduler.start`](../app/services/scheduler.py#L503) registers `_sync_live_matches_job` every 60 seconds with `max_instances=1` (`scheduler.py:510-514`).
3. `_sync_live_matches_job` acquires the scheduler's session-level PostgreSQL advisory lock using `LIVE_MATCH_SYNC_LOCK_KEY` (`scheduler.py:879-887`, `1647-1651`). It first selects due finalizations and calls `_finalize_match_sync_candidates` (`scheduler.py:888-899`).
4. `_should_sync_live_matches` checks local API-Football rows. Any status in `LIVE_STATUSES`, including `P`, makes the job eligible; near-kickoff `NS` rows are also eligible under the configured window (`scheduler.py:832-870`).
5. If eligible, the scheduler enters `run_with_resource_lock(db, "live_sync", "global", sync_live)` (`scheduler.py:904-939`). Inside the callback it invokes `football_service.sync_live_matches`, commits successful database changes, updates the active-match registry, and invalidates the live-match cache (`scheduler.py:908-929`).
6. After the callback, it finalizes candidates discovered by the live sync while the scheduler-wide session advisory lock remains held (`scheduler.py:935-946`). The transaction-scoped resource lock itself is released by commit/rollback, not by the helper's callback exit.

### 3.2 Provider request, parsing, and local identity

- [`FootballAPIService.sync_live_matches`](../app/services/football.py#L180) delegates to `FixtureSyncService.sync_live_matches`.
- [`FixtureProvider.get_live_fixtures`](../app/providers/fixture_provider.py#L49) calls `GET /fixtures` with `live=all`. The normal live request does not use date, league, or season filters.
- The fixture service requires a dictionary with a list-valued `response`. For each payload it extracts the fixture ID, `status.short`, `status.elapsed`, and `goals.home/away`; it converts missing/falsey elapsed and goals to zero (`fixture_sync_service.py:1160-1222`).
- The provider status short code is copied into the local state; there is no separate status translation table in this path. `P` is included in `LIVE_STATUSES`; `FT`, `AET`, and `PEN` are finalization terminal statuses (`football.py:39-40`, [`final_match_sync_service.py`](../app/services/final_match_sync_service.py#L13)).
- The service looks up the row by `(provider="api-football", provider_fixture_id)` (`fixture_sync_service.py:1229-1233`). It does **not** restrict this lookup to locally live statuses.
- It calls `MatchRepository.update_live_state` with the parsed four-field result tuple (`fixture_sync_service.py:1234-1242`). That repository update is unconditional (`match_repository.py:21-45`).
- If an update moves a prior locally-live status to `FT`, `AET`, or `PEN`, the service ensures a durable finalization row and returns a candidate unless the row is already `SUCCESS` or `EXHAUSTED` (`fixture_sync_service.py:1250-1260`).
- Local matches still marked live but absent from the returned provider ID set are separately registered as finalization candidates (`fixture_sync_service.py:1263-1281`); absence does not itself update their match row.

### 3.3 Cache and active-match side effects

On successful scheduler execution, database commit happens before active-match registry updates and `live_matches` cache invalidation (`scheduler.py:908-929`). These side effects are not a persistence guard. If a new terminal guard rejects an incoming stale update, the service must not publish the rejected incoming status to `active_match_updates`; current code adds the incoming status after any `rowcount == 1` update (`fixture_sync_service.py:1244-1253`).

## 4. Current Lock and Concurrency Map

`run_with_resource_lock` hashes a namespaced identity and acquires `pg_try_advisory_xact_lock` (`resource_lock.py:11`, `27-37`, `41-67`). It is transaction-scoped and process-shared through PostgreSQL. `release_resource_lock` only logs scope exit; commit or rollback ends the lock lifetime (`resource_lock.py:69-90`).

| Writer | Scheduler/session advisory lock | Transaction resource lock | Present coordination |
|---|---|---|---|
| Normal scheduled Live Sync | `LIVE_MATCH_SYNC_LOCK_KEY = 9342002` | `live_sync:global` | Duplicate scheduled Live Sync runs across worker instances are serialized by the session lock. It does not share either lock identity with fixture date/season sync. |
| Scheduled Finalization Sync | Same `9342002`; `_finalize_match_sync_candidates` can be called with `live_lock_held=True` | `live_sync:global` during the provider/finalization operation | Scheduled finalization and scheduled live job share the scheduler-wide lock. This does not guard other writer paths such as daily fixture upsert. |
| Scheduled Daily Fixture Sync | `DAILY_FIXTURE_SYNC_LOCK_KEY = 9342003` | `fixture_query:global` | Not the same session key or resource identity as Live Sync. |
| Scheduled Daily Repair | `REPAIR_DAILY_MATCHES_LOCK_KEY = 9342004` | `fixture_query:global` | Shares the transaction identity with daily sync, but not its session lock. Repair runs yesterday and today, committing each successful date; the transaction lock can be released between those transactions. |
| Full-season/date fixture API sync | No scheduler session lock at route level; [`app/api/matches.py`](../app/api/matches.py#L960) wraps both calls in a `fixture_query:global` resource lock and commits inside the callback | `fixture_query:global` | Can contend with daily fixture/repair but does not use `live_sync:global`. |

The integer scheduler locks are acquired on a dedicated pinned connection and released on that same connection (`scheduler.py:1582-1644`). Failure to release invalidates the connection and raises; the scheduler lock is released in a `finally` block (`scheduler.py:879-956`). The local Kubernetes worker Deployment and HPA both specify one replica (`k8s/worker-deployment.yaml:9`, `k8s/worker-hpa.yaml:12-13`), and the compose file has one worker service. These are configuration facts only; the deployed process count is **NOT VERIFIED**.

### Concurrency finding

There is **no single per-match lock identity shared by every writer**. Normal Live Sync and finalization use `live_sync:global`; fixture date/season paths use `fixture_query:global`. Daily Sync and Repair share the latter transaction lock but have different session advisory keys. Therefore:

- scheduled Live Sync and scheduled Finalization are coordinated with each other at job scope;
- daily date/repair writes can overlap with Live Sync/finalization;
- date/repair operations can contend with each other while their shared transaction lock is held, but can interleave across separately committed repair dates;
- process-local `max_instances=1` does not coordinate distinct job IDs or separate worker processes.

## 5. Current Data Persistence Behavior

### 5.1 Normal live updates

`MatchRepository.update_live_state` runs a textual SQL `UPDATE` that writes only:

- `status`
- `elapsed`
- `home_score`
- `away_score`

Its predicate is only the local match ID; it does not check old status, incoming status, score version, finalization state, timestamp, or row version. It returns `rowcount == 1`, which cannot distinguish a missing row from a future conditional rejection unless the return contract is extended.

### 5.2 Daily, repair, and season fixture updates

The shared `_process_sync_with_candidates_in_batch` path used by `sync_daily_fixtures`, Daily Repair, and `sync_full_season` performs a PostgreSQL upsert (`fixture_sync_service.py:438-463`, `610-656`, `1042-1059`, `1112-1125`). Its conflict update unconditionally includes status, elapsed, home/away scores and fixture metadata. It has no status regression predicate or terminal-result-specific update policy.

This is important to scope: a repository-only guard added exclusively to `update_live_state` would not protect terminal rows from the fixture upsert.

### 5.3 Match fields and result scope

The `Match` model stores match identity, league/season, fixture time, status, elapsed, home/away teams, home/away scores, venue, referee, and creation/update timestamps (`models/match.py:8-67`). It does not have penalty-score columns.

Penalty scores exist on `AnalyticsH2HSnapshot` as `penalty_home_score` and `penalty_away_score` (`models/analytics.py:98-165`); that is an analytics/H2H projection, not a field written by normal `live_sync` or `FinalMatchSyncService.update_live_state`. No penalty score persistence in the canonical Match update was found.

### 5.4 Timestamps and transaction boundary

The model declares `Match.updated_at` with SQLAlchemy `onupdate`, but the text `UPDATE matches` does not name `updated_at`, and the fixture `ON CONFLICT DO UPDATE` set list also does not include it (`match.py:67`, `match_repository.py:34-44`, `fixture_sync_service.py:628-655`). The initial migration does not add a database trigger for ordering updates (`alembic/versions/0001_initial.py`, matches table).

The finalization repository locks its `match_finalization` row with `FOR UPDATE`, not the corresponding `matches` row (`match_finalization_repository.py:24-31`). This serializes finalization state claims, not all writes to match result fields. The database row update is atomic, but the current unconditional write is not safe against stale data.

No runtime schema, trigger, or production database state was queried. Whether any out-of-band database trigger exists is **NOT VERIFIED**.

## 6. Proposed Target Design Assessment

### Design A — Terminal Status Protection

**Compatible, with an atomicity adjustment.** Keep `P` in the live/nonterminal set. Keep the finalization terminal set as `FT`, `AET`, and `PEN`; do not map `P` to `PEN`. A valid provider terminal transition from a nonterminal local row must remain accepted.

The terminal-to-nonterminal guard belongs at the **persistence boundary** (the existing repository/update path), because only that layer can apply it atomically at write time. `FixtureSyncService` should retain orchestration/transition classification. A service-only `if local.status in terminal` check is vulnerable to a race between the read and the write. The same invariant must be implemented in the fixture upsert path, not only `update_live_state`.

For normal Live Sync specifically, an already-terminal row should not accept a later all-live snapshot's result tuple. A terminal response from a local nonterminal row remains allowed. Do not equate `FT`, `AET`, and `PEN`; preserve the provider's confirmed status. Define explicitly whether a trusted terminal-to-terminal correction is allowed only through an authoritative finalization path, because the current source has no version/timestamp to decide whether two terminal snapshots are ordered.

### Design B — Terminal Data Integrity and metadata

The result tuple that normal Live Sync writes (`status`, `elapsed`, `home_score`, `away_score`) must be guarded together, not just status. Otherwise stale `P` could preserve `PEN` status but overwrite its score or elapsed time.

Suggested field treatment:

| Field group | Recommended treatment | Reason |
|---|---|---|
| `status`, `elapsed`, `home_score`, `away_score` | Protect as one result snapshot after local status is `FT`, `AET`, or `PEN` when the writer is a live/non-authoritative snapshot. | These are exactly the fields normal Live Sync writes. |
| Penalty result fields | No canonical Match fields exist. If an analytics projection writes them, preserve/update them under that projection's own terminal-result policy; do not imply this live repository writes them. | They are separate from the Match row. |
| `provider`, `provider_fixture_id`, `local_match_id` | Treat as identity, not provider-refreshable metadata. | Canonical identity must remain stable. |
| League/team IDs and names, season, fixture time, venue/referee IDs and display metadata | May remain eligible for updates through the existing identity-resolving fixture path, with separate validation and null/identity semantics. Treat fixture time, team/league IDs, and season as special rather than blindly copying stale values. | These fields are not part of normal live-state update; the full fixture upsert updates them. |
| `created_at`, `updated_at` | Do not use for provider snapshot ordering without defining and explicitly maintaining their semantics. | Current raw updates do not set `updated_at`; local write time would not establish provider freshness anyway. |

Do not block all Match updates merely because a result is terminal. Instead, separate result-field policy from independently validated metadata. If the fixture upsert continues updating result and metadata in one statement, its conflict update must conditionally preserve the result tuple while allowing approved metadata columns to update.

### Design C — Finalization and Live Sync coordination

The scheduled live job already runs due finalizations before normal live updates and processes newly discovered finalization candidates afterward. It also holds the scheduler-wide Live Sync advisory lock through the cycle. This is useful ordering, but it does not protect against Daily Repair/fixture upsert, and it does not make the result write itself stale-safe.

Recommended coordination:

1. Keep the existing scheduler-level session advisory locks to coalesce duplicate executions of each job.
2. Reuse `run_with_resource_lock` for a **shared per-local-match transaction lock identity** around each match's database persistence in normal Live Sync, finalization, daily/repair, and full-season fixture sync.
3. Keep provider network reads outside the per-match lock when possible; acquire the lock for local re-read/validation/persistence. This limits lock hold time.
4. Keep a database-atomic terminal predicate/result policy at persistence as a second line of defense. The lock only coordinates writers that adopt the same identity; the predicate protects against stale reads and an uncoordinated writer.
5. Do not use “has any finalization row” as a blanket reason to skip live data. `PENDING`/`RUNNING` are work states, not proof that the Match row is already final. `SUCCESS` can be an optimization hint, not a data-integrity guard.

If `run_with_resource_lock` is used, every participating writer must use exactly the same resource type and canonical local Match ID. Current separate global identities do not meet that requirement.

### Design D — Concurrency and locking

The current global locks are adequate for same-job coalescing but not consistent ownership for a particular Match across all write paths. Reuse the current PostgreSQL transaction advisory lock mechanism; do not add another lock service.

At present, a match-specific database lock is not acquired for live-result persistence. Finalization `FOR UPDATE` applies to the finalization state row only. A shared per-Match resource lock plus atomic conditional writes is the minimal adjustment. A separate `SELECT ... FOR UPDATE` on `matches` is not inherently necessary if the result update/upsert predicate itself enforces the invariant atomically. If choosing a row-lock approach instead, it must be taken by all competing writers before reading and writing the result tuple and be held through commit.

### Design E — Stale provider data

The inspected path stores no provider update timestamp, provider revision, or monotonically increasing status version. `elapsed` is not a reliable ordering token (halftime, extra time, stoppage, penalty statuses, corrections, and provider resets make simple numerical ordering unsafe). The local `updated_at` is not maintained by the inspected SQL paths and, even if it were, would describe local write time rather than provider data freshness.

Without a provider ordering token, the system can still reliably prevent the critical class of regression—terminal local result overwritten by nonterminal live data—using an atomic terminal guard. It cannot prove which of two nonterminal snapshots or two terminal score corrections is newer. Do not add a timestamp column or migration unless reliable provider ordering is established and a separate requirement justifies it.

## 7. Architectural Compatibility Matrix

| Question | Classification | Evidence and answer |
|---|---|---|
| 1. Can Terminal Status Protection be implemented in the current architecture? | **COMPATIBLE** | The existing repository is the persistence boundary; `FixtureSyncService` already orchestrates live state updates and finalization candidates. No new service is needed. |
| 2. Correct architectural layer? | **COMPATIBLE WITH ADJUSTMENT** | Put the atomic invariant in repository/persistence SQL; keep provider parsing and lifecycle orchestration in Service/SyncService. An application-only check is insufficient because of TOCTOU races. |
| 3. Protect result fields without blocking metadata? | **COMPATIBLE WITH ADJUSTMENT** | Normal live writes only the four result fields; fixture upsert writes result plus metadata. Split their policies in persistence; validate identity/fixture metadata independently. |
| 4. Can Normal Live Sync and Finalization reuse existing locks? | **COMPATIBLE WITH ADJUSTMENT** | They currently share scheduler key `9342002` and `live_sync:global`. Reuse `run_with_resource_lock`, but move/extend coordination to one per-Match identity shared with every writer. |
| 5. Can Daily Repair and other sync jobs share lock ownership? | **COMPATIBLE WITH ADJUSTMENT** | Existing resource locks are PostgreSQL transaction advisory locks, but current identities differ (`fixture_query:global` vs `live_sync:global`). Use the same per-Match identity; retain job locks separately. |
| 6. Is repository behavior safe against stale updates? | **NOT COMPATIBLE** | `update_live_state` writes status/elapsed/scores unconditionally by ID. The fixture upsert has the same issue for its result columns. |
| 7. Are transaction boundaries sufficient? | **COMPATIBLE WITH ADJUSTMENT** | Writes are committed by scheduler callbacks; transaction advisory locks last until commit/rollback. Current boundaries do not coordinate different lock identities. Ensure the shared per-Match lock spans the protected write through commit. |
| 8. Is database-level conditional update or row lock necessary? | **COMPATIBLE WITH ADJUSTMENT** | A database-atomic terminal predicate is necessary; an app precheck is racy. A separate explicit row lock is optional if all relevant writes use the atomic conditional update/upsert. |
| 9. Can protections avoid a new table? | **COMPATIBLE** | Existing `matches` fields and PostgreSQL advisory locks are sufficient for terminal regression protection and coordination. |
| 10. Is an Alembic migration necessary? | **COMPATIBLE** | No migration is needed for conditional SQL or reuse of the existing locks. A migration would only be needed if a new persisted version/timestamp/state column were separately justified; this audit recommends none. |
| 11. What tests need extension? | **COMPATIBLE WITH ADJUSTMENT** | Extend live-state, repository/upsert, scheduler lock, and finalization interaction tests; minimum set is in Section 9. |
| 12. What regression risks remain? | **COMPATIBLE WITH ADJUSTMENT** | No provider ordering exists; terminal-to-terminal corrections and nonterminal ordering need explicit policy. Rejected writes must not publish stale active-match/cache state. |

## 8. Failure Scenario Assessment

| Scenario | Current behavior | Expected/target behavior | Result and required adjustment |
|---|---|---|---|
| **1 — Normal stale response:** local `FT`, all-live endpoint returns `LIVE` | **Fails.** The provider fixture is looked up regardless of local status, then status/scores/elapsed are overwritten. | Keep final result unchanged. | Add an atomic result-tuple guard to normal live persistence. Add equivalent protection to any other path that can write result fields. |
| **2 — PEN finalization:** local `P`, exact-ID finalizer writes `PEN`, later all-live response returns `P` | **Fails for the Match row.** The finalizer writes confirmed `PEN` through `update_live_state`; later normal live sync unconditionally writes `P` and its score/elapsed. `P` remains classified live, not terminal. | Keep `PEN` and final result intact; never synthesize `PEN` from `P`. | Guard the whole result tuple atomically. Current Match row has no penalty-score columns; any separate penalty projection must be governed independently. |
| **3 — Normal Live Sync and Daily Repair update same match simultaneously** | **Can overlap.** Session keys and global transaction identities differ (`live_sync` vs `fixture_query`). Daily Repair's fixture upsert can update the same result columns. | No conflicting/regressive persistence. | Shared per-match transaction lock for both paths, plus conditional write policy. A lock conflict must be surfaced as defer/skip, not treated as a successful write. |
| **4 — Normal Live Sync starts; finalization starts before its update commits** | **Scheduled path:** duplicate scheduled live/finalization jobs share the same session advisory key and are serialized. **Other writer paths:** fixture upsert does not share that key; direct service calls also do not acquire the job-level lock themselves. | Finalization must not be overwritten by stale live data. | Keep scheduler coalescing, but rely on shared per-Match persistence coordination and atomic guard across all writers. This avoids depending on call topology/order. |
| **5 — Provider temporarily returns older/inconsistent status** | A terminal local row can regress; among nonterminal states, there is no source ordering check. | No terminal status regression. | Terminal protection is achievable now. Ordering among nonterminal snapshots remains unresolved without a reliable provider version/timestamp; do not infer freshness from elapsed time alone. |
| **6 — Legitimate `LIVE → FT/AET/PEN` transition** | **Passes the basic transition.** Incoming terminal status and scores are written; live-to-terminal status creates finalization candidate. | Accept terminal update and preserve distinctions. | Keep this behavior. Guard only against stale writes to already-terminal rows; test each terminal status explicitly. |
| **7 — Worker restart or multiple workers** | The checked manifests set worker replicas and HPA max to one. In code, `max_instances=1` is process-local; PostgreSQL session advisory locks coordinate same scheduler job across processes and transaction advisory locks are process-shared. Runtime/deployed count is not verified. | Protection should remain across processes/restarts. | Reuse PostgreSQL locks, but ensure each competing writer uses the same per-Match identity. Terminal conditional writes remain necessary if a path bypasses the lock. |
| **8 — Metadata update after finalization** | Normal live path does not write non-result metadata. Daily/full fixture upsert writes both metadata and result fields together, unconditionally. | Permit only explicitly safe metadata updates; preserve finalized result. | Keep approved metadata refreshes separate from the guarded result tuple. Identity and scheduling fields (team/league IDs, season, fixture time) need validation; do not block every metadata write or blindly copy all provider fields. |

## 9. Required Test Coverage

Existing tests cover parts of the lifecycle but not the target invariant:

- `test_live_sync_phase6.py::test_live_sync_reports_persisted_local_match_status` parameterizes incoming `P`, `FT`, `AET`, and `PEN`, but the fake local row is always `1H`; it does not test terminal-local regression.
- `test_final_match_sync_phase3.py::test_normal_live_fixture_updates_state_without_missing_candidate` verifies an ordinary live update, not a terminal local state.
- `test_final_match_sync_phase3.py` verifies `FT/AET/PEN` finalization writes and that `P` waits without updating the Match row. It does not run a stale normal live update after successful finalization.
- `test_sync_boundary_freeze.py::test_live_sync_updates_terminal_match_without_child_orchestration` supplies a fake local row without its current status; it verifies terminal incoming data does not invoke child orchestration, not terminal-state preservation.
- `test_batch_fixture_locking.py` separately verifies `live_sync:global` and `fixture_query:global`; it does not assert shared per-Match ownership across live, repair, and finalization.
- `test_final_match_sync_phase3.py::test_concurrent_attempt_does_not_duplicate_provider_request` concerns duplicate finalization attempts, not concurrent live-vs-repair writes.
- Scheduler tests cover commit/cache ordering and lock cleanup, but not stale-result rejection.

Minimum additions/adjustments, not executed:

1. Repository-level atomic test: `FT`, `AET`, or `PEN` + incoming `LIVE`/`P` leaves all result fields unchanged.
2. Repository and fixture-upsert tests: terminal status and the complete result tuple remain unchanged on stale nonterminal input; metadata allowed by policy can still update.
3. Positive transition tests for `LIVE → FT`, `LIVE → AET`, and `LIVE → PEN`; verify `P` is not treated as terminal or converted.
4. Finalization interaction test: local `P`, successful `PEN` exact-ID finalization, then stale all-live `P`; `PEN` and scores remain.
5. Conditional-write race/concurrency test using PostgreSQL or an equivalent integration setup: finalization wins/terminal row cannot be overwritten by a concurrent stale write.
6. Lock identity tests proving normal live, finalization, Daily Repair, daily sync, and fixture API persistence acquire the same per-Match transaction resource identity.
7. Tests distinguishing a guard-rejected update from a missing row so active-match registry and cache side effects do not advertise rejected input.
8. Policy test for terminal-to-terminal result correction and a documented test boundary for nonterminal ordering (not inferable from source timestamps).

## 10. API Usage and Performance Impact

- **Provider calls/credits:** The proposed persistence guards and lock reuse require no additional provider calls. No provider quota or actual credit cost is configured/verified by the inspected source; quota is **NOT VERIFIED**.
- **Processing time:** Terminal predicates add no provider latency. A per-Match lock adds a short database lock-acquisition operation around persistence; provider fetches should remain outside the per-Match lock to avoid holding it over network latency.
- **Database queries:** Normal Live Sync already performs local identity lookup and an update per returned fixture. A conditional `UPDATE` can enforce terminal status without an extra read. Per-Match lock acquisition adds an advisory-lock query per persisted match unless batched/reused in a transaction. The exact overhead is **NOT MEASURED**.
- **Lock contention:** Per-Match locks narrow contention relative to a shared `fixture_query:global` lock. Contended writes should defer/retry and be observable; do not silently proceed without protection.
- **Scheduler duration:** No additional polling interval or provider request is needed. The existing live scheduler remains at 60 seconds. No performance benchmark was run.

## 11. Minimal Required Design Adjustments

Priority order:

1. **Define the protected result policy:** terminal local `FT/AET/PEN` must not accept nonterminal live state. Protect status, elapsed, and home/away scores as one tuple. Keep `P` nonterminal. Define whether terminal-to-terminal corrections are allowed only through a confirmed finalization path.
2. **Enforce it atomically in persistence:** add the predicate to existing repository update SQL and the fixture upsert conflict policy. A service pre-read alone is not safe.
3. **Coordinate every writer with existing locks:** use the same `run_with_resource_lock` resource type and local Match ID for normal live, finalization, daily fixture sync, Daily Repair, and full-season fixture persistence. Keep existing session advisory locks for job-level duplicate suppression.
4. **Separate result fields from metadata writes:** allow explicitly validated non-result metadata updates while preserving finalized result columns; treat identity and fixture-time fields specially.
5. **Return a precise persistence outcome:** distinguish “updated”, “protected/rejected as stale”, and “row missing”. Only publish persisted status to the active-match registry and perform relevant result side effects.
6. **Extend focused tests** listed in Section 9, including one real PostgreSQL concurrency/conditional-write check where test infrastructure permits.

No new synchronization system, worker, queue, table, or general architecture redesign is warranted by the inspected evidence.

## 12. Database Migration Assessment

**No Alembic migration is required for the recommended minimal design.**

The existing Match row already contains the status, elapsed, and score fields needed for an atomic condition. Existing PostgreSQL transaction advisory locks support a shared per-Match key. The finalization state table is not a substitute for a Match-row guard, but no additional finalization state is needed to prevent the identified regression.

Do not add a provider timestamp/version column without establishing a reliable provider ordering source and a requirement for ordering nonterminal or terminal-to-terminal snapshots. The current report does not verify provider update timestamp availability or query production database metadata.

## 13. Implementation Dependency Order

This is a design ordering only; no implementation was performed.

1. Confirm terminal-result correction policy and exact metadata allowlist.
2. Define one persistence result contract for “updated / protected / missing” and one canonical per-Match lock identity.
3. Apply the result guard to `MatchRepository.update_live_state` and the fixture conflict-upsert path.
4. Wire normal live, finalization, daily/repair, and full-season persistence through the shared per-Match coordination while retaining job locks.
5. Ensure rejected writes do not publish stale active-match status/cache effects.
6. Add the focused unit/integration tests before deployment; test cross-process behavior against PostgreSQL if available.

## 14. Remaining Risks and NOT VERIFIED Items

- **NOT VERIFIED:** Production database schema, constraints, triggers, and transaction isolation; no live database was queried.
- **NOT VERIFIED:** Running worker count and whether production configuration matches the local checked manifests.
- **NOT VERIFIED:** Provider's delivery ordering, temporary inconsistency behavior, and whether it exposes a reliable update/version timestamp.
- **NOT VERIFIED:** Provider quota/credit costs and actual live endpoint response freshness.
- **Source limitation:** No reliable source-ordering/version field is read or persisted. Terminal-to-nonterminal regression can be blocked; general stale ordering among nonterminal snapshots and terminal-to-terminal score corrections cannot be resolved from current persisted data.
- **Operational risk:** A lock conflict must not be treated as a successful no-op if the corresponding fixture remains eligible; retry/defer visibility is needed.
- **Scope risk:** Protecting only `update_live_state` leaves daily/repair/season upserts unsafe. Protecting only Normal Live Sync leaves alternate result writers unsafe.
- **Status scope:** The requested protected result terminals are `FT`, `AET`, and `PEN`. Existing code also classifies `CANC`, `ABD`, `AWD`, `WO`, and `PST` as stop/finished statuses for other purposes. Whether these statuses should receive immutable-result behavior needs an explicit policy; they should not be silently conflated with confirmed final score statuses.
- No tests were executed, as explicitly prohibited. No provider requests, sync jobs, API endpoints, configuration changes, database queries, or service restarts were performed.

## 15. Final Verdict

# READY AFTER DESIGN ADJUSTMENT

The target design is architecturally sound and can be implemented with the current Provider → Service → SyncService → Repository → Database responsibilities. It is not ready to implement exactly as a guard in only the Normal Live Sync service or repository method: the fixture date/repair/season upsert is also a result writer, the existing lock identities are inconsistent across these paths, and an application-level status precheck is racy.

The smallest reliable scope is: atomic terminal-result protection in both existing persistence paths; one shared per-Match transaction resource lock for all competing match-result writers; preservation of the distinction among `FT`, `AET`, and `PEN`; explicit field-level metadata policy; and focused regression/concurrency tests. No new table or migration is indicated.
