# FIXTURE SYNC STATUS TRANSITION — ROOT-CAUSE AUDIT

## 1. Executive Summary

The audit confirms a **scheduler / active-match selection gap**, with a secondary **terminal cleanup selection gap**.

The provider status mapping and terminal transition comparison are not the primary defect:

* API-Football `status.short` is copied into `Match.status`.
* `NS -> FT`, `LIVE -> FT`, `1H -> FT`, and `2H -> FT` all satisfy the transition predicate when the fixture is actually processed.
* Finalization `REQUIRED` is created for `previous_status in NON_TERMINAL_STATUSES` and `new_status in FINAL_LINEUP_TERMINAL_STATUSES`.

The problem is that the 60-second live synchronization path does not guarantee that a locally `NS` match whose provider status becomes terminal will be fetched again. Its stale cleanup query only selects local matches already in the live-status set. The 15-minute lineup refresh selects future `NS` matches, not recently completed matches. The daily fixture job may eventually catch the fixture if its date window is run, but that is not an immediate terminal-transition guarantee.

## 2. Problem Statement

Observed risk:

```text
API-Football provider = FT
Fover local Match.status = NS
```

This can persist when the provider completes a fixture before Fover has observed a live status for that fixture, or when the local fixture was not selected by the relevant sync job after completion.

## 3. Authoritative Documents

Read and respected:

* `docs/PHASE_6_1_ARCHITECTURE_FREEZE.md`
* `docs/PHASE_6_2_FAILURE_STATE_MATRIX_FREEZE.md`
* `docs/PHASE_6_3_IMPLEMENTATION_REPORT.md`
* `docs/PHASE_6_4_TEST_REPORT.md`
* `docs/PHASE_6_5_REAL_E2E_REPORT.md`

No production code, schema, status, finalization record, lineup, Analytics row, Player, or Event was modified.

## 4. Runtime Architecture

```text
API-Football
  -> FootballAPIClient
  -> FixtureProvider
  -> FixtureSyncService
  -> Match identity/team resolution
  -> Match upsert
  -> local Match.status
  -> terminal transition predicate
  -> MatchLineupFinalizationRepository.create_required
  -> finalization retry
```

Transaction owner:

* API route
* scheduler job
* outer sync orchestration

Repositories and status parsing do not own global commit/rollback.

## 5. Complete Status Pipeline

### Provider source

`FixtureProvider` calls API-Football `/fixtures` endpoints:

* `/fixtures?live=all`
* `/fixtures?date=...`
* `/fixtures?league=...&season=...`
* `/fixtures?ids=...`

The provider raw field is:

```text
response[].fixture.status.short
```

`FixtureSyncService.parse_fixture_to_match()` copies that value into `MatchCreate.status`, which is then used in the Match upsert.

### Database write

`FixtureSyncService._process_sync_with_candidates()`:

1. filters allowed leagues
2. parses the fixture
3. resolves teams
4. reads the existing Match by `(provider, provider_fixture_id)`
5. records `previous_status`
6. upserts the Match row with the new provider status
7. flushes
8. evaluates terminal transition
9. creates finalization `REQUIRED` when eligible

The outer caller commits or rolls back.

## 6. Provider Status Mapping

The implementation preserves API-Football status strings; it does not translate them into another database status vocabulary.

| Provider status | Local status | Terminal? | Live? | Finalization transition eligible? |
|---|---|---:|---:|---:|
| `NS` | `NS` | No | No | As previous status: Yes if new status is terminal |
| `TBD` | `TBD` | No | No | Yes as previous non-terminal |
| `LIVE` | `LIVE` | No | Yes | Yes as previous non-terminal |
| `1H` | `1H` | No | Yes | Yes as previous non-terminal |
| `HT` | `HT` | No | Yes | Yes as previous non-terminal |
| `2H` | `2H` | No | Yes | Yes as previous non-terminal |
| `ET` | `ET` | No | Yes | Yes as previous non-terminal |
| `BT` | `BT` | No | Yes | Yes as previous non-terminal |
| `P` | `P` | No | Yes | Yes as previous non-terminal |
| `FT` | `FT` | Yes for lineup finalization | No | Yes as new status |
| `AET` | `AET` | Yes for active/fixture lifecycle | No | Yes as new status only where lineup terminal set includes it; current lineup set does not |
| `PEN` | `PEN` | Yes | No | Yes as new status |
| `CANC` | `CANC` | Active-terminal only | No | No for lineup finalization |
| `ABD` | `ABD` | Active-terminal only | No | No for lineup finalization |
| `AWD` | `AWD` | Active-terminal only | No | No for lineup finalization |
| `WO` | `WO` | Active-terminal only | No | No for lineup finalization |

## 7. Terminal Status Definitions

There are multiple status sets:

* `FixtureSyncService.FINISHED_STATUSES`: `FT`, `AET`, `PEN`, `CANC`, `ABD`, `AWD`, `WO`
* `ACTIVE_MATCH_TERMINAL_STATUSES`: finished statuses plus `PST`
* `FINALIZATION_TERMINAL_STATUSES`: same active terminal set
* `FINAL_LINEUP_TERMINAL_STATUSES`: only `FT`, `AET`, `PEN`
* `FootballAPIService.FINISHED_STATUSES`: only `FT`, `AET`, `PEN`, `CANC`, `ABD`, `AWD`, `WO`
* `FinalLineupFinalizationRepository.FINAL_LINEUP_TERMINAL_STATUSES`: only `FT`, `AET`, `PEN`
* `EventService.FINISHED_STATUSES`: only `FT`, `AET`, `PEN`

This is an inconsistency, but it is not the primary explanation for `NS -> FT`. The `FT` case is included in every relevant set.

## 8. Terminal Transition Detection

The exact predicate is:

```python
if previous_status in NON_TERMINAL_STATUSES \
   and normalized_status in FINAL_LINEUP_TERMINAL_STATUSES:
    create_required(local_match_id)
```

Current `NON_TERMINAL_STATUSES`:

```text
NS, TBD, 1H, 2H, HT, ET, LIVE, BT, P
```

Current `FINAL_LINEUP_TERMINAL_STATUSES`:

```text
FT, AET, PEN
```

Therefore:

| Transition | Terminal transition? | Evidence |
|---|---:|---|
| `NS -> FT` | Yes | `NS` is non-terminal and `FT` is lineup-terminal |
| `LIVE -> FT` | Yes | `LIVE` is non-terminal and `FT` is lineup-terminal |
| `1H -> FT` | Yes | `1H` is non-terminal and `FT` is lineup-terminal |
| `2H -> FT` | Yes | `2H` is non-terminal and `FT` is lineup-terminal |
| `HT -> FT` | Yes | `HT` is non-terminal and `FT` is lineup-terminal |
| `ET -> FT` | Yes | `ET` is non-terminal and `FT` is lineup-terminal |
| `P -> FT` | Yes | `P` is non-terminal and `FT` is lineup-terminal |

`NS -> FT` is not rejected by the transition predicate.

## 9. NS → FT Analysis

If a fixture payload containing `FT` reaches `_process_sync_with_candidates()` while the local row is `NS`:

```text
previous_status = NS
normalized_status = FT
```

The predicate is true and `create_required()` is called.

The actual failure mode is upstream selection: a locally `NS` fixture is not included in the live stale-match cleanup because `MatchRepository.get_live_stale()` filters only local statuses in `LIVE_STATUSES`.

## 10. LIVE → FT Analysis

`LIVE -> FT` is supported by the transition predicate.

The live sync job fetches `/fixtures?live=all`. When a local live match disappears from that feed, `get_live_stale()` selects it and the service fetches it by provider fixture ID. If that response is `FT`, the transition is detected.

This path is therefore supported for matches that were already locally observed as live.

## 11. Scheduler Analysis

### Live sync

* Job: `_sync_live_matches_job()`
* Interval: every `60` seconds
* Provider source: `/fixtures?live=all`
* Gate: local Match rows in a broad 24-hour past/future window
* Stale cleanup: fetches local live-status matches absent from current provider live feed

### Daily sync

* Job: `_sync_daily_fixtures_job()`
* Schedule: daily at `00:01` Myanmar time
* Provider source: `/fixtures?date=...`
* It processes fixtures returned for that date.

### Daily repair

* Job: `_repair_daily_matches_job()`
* Schedule: daily at `02:00` Myanmar time
* Re-syncs yesterday and today.

### Lineup refresh

* Job: `_refresh_lineups_job()`
* Interval: every `15` minutes
* Selects only local `NS` matches whose `match_time` is in the next 90 minutes.
* It is not a completed-fixture status refresh job.

## 12. Active-Match Selection Gap

The live stale query is:

```text
Match.status IN LIVE_STATUSES
AND match_time >= now - 24 hours
AND provider_fixture_id NOT IN current provider live IDs
```

It does not select local `NS` matches.

Consequently, if the local row remains `NS` until the provider has already moved to `FT`, the 60-second live job does not guarantee that fixture will be fetched by provider ID. The daily date sync/repair may eventually find it, but this is dependent on schedule/date coverage.

The next live-sync cycle therefore does **not** guarantee `NS -> FT`.

## 13. Terminal Cleanup Analysis

`_sync_active_match_registration()` removes an active match for terminal statuses, but it is called after the Match upsert and transition check in `_process_sync_with_candidates()`.

For a fixture that is actually processed, cleanup is after local status persistence and after finalization requirement creation. That ordering is correct.

The defect is that a local `NS` fixture may never enter the processing set through the live stale path. This is a selection gap, not an early cleanup-order defect.

## 14. Transaction Analysis

For a processed fixture:

```text
provider response
  -> parse and identity/team resolution
  -> read previous local status
  -> Match upsert
  -> flush
  -> terminal transition detection
  -> create_required and flush
  -> outer commit
```

A savepoint is used per fixture inside the fixture sync transaction. The API/scheduler caller owns commit and rollback.

Possible `Provider=FT / Local=NS` causes:

* **Possible:** fixture was not selected by the live job due to local `NS` filter.
* **Possible:** provider fixture was not included in daily date response/repair window.
* **Possible:** transaction rollback or exception before outer commit after processing.
* **Impossible from code path:** status mapping silently changing `FT` to `NS`; parser copies `status.short` directly.
* **Not evidenced:** current database evidence of a specific rollback causing the observed mismatch.

## 15. Cache Analysis

Match status is not used as a dedicated status cache in the audited FixtureSync path. Live-match cache invalidation occurs after successful commit. Cache staleness can affect presentation of live match lists, but it does not explain PostgreSQL retaining `NS` when the provider payload was not processed.

DB status, service response, and cache state must be distinguished. The primary observed risk is DB selection/polling, not cache substitution.

## 16. Database Evidence

Read-only current population:

* Local `NS` matches: `818`
* Local live-status matches: `7`
* Local terminal matches: `922`
* Finalization `REQUIRED`/`RETRYABLE`: `4`

A provider probe of recent local `NS` rows did not produce a stable provider-terminal mismatch during this audit. A live candidate was observed separately, but no terminal transition occurred during the bounded poll.

No database mutation was performed.

## 17. Runtime Log Evidence

The code emits relevant events:

* `FINAL_LINEUP_REQUIRED`
* `LINEUP_SYNC_FAILED`
* `FINAL_LINEUP_SUCCESS`
* `LIVE_SYNC_*`
* `FINAL_LINEUP_RETRY`

No persisted log evidence was available in the workspace proving a specific `Provider FT -> rollback` sequence for an individual current fixture. The code path and selection predicates are sufficient to confirm the polling gap.

## 18. Test Coverage

Existing tests cover:

* terminal transition candidates for `FT`, `AET`, and `PEN` from a prior `LIVE` status
* finalization state handling
* locks, retry, transaction, and cache ordering

Existing tests do not explicitly cover:

* `NS -> FT`
* `LIVE -> FT` through the real stale cleanup path
* `1H -> FT`
* `2H -> FT`
* `HT -> FT`
* `ET -> FT`
* `P -> FT`
* live job selection of a locally `NS` match that is already provider-terminal

## 19. Root Cause

### Primary root cause

```text
C. ACTIVE-MATCH SELECTION GAP
```

The live polling path only refreshes provider-live fixtures and locally live matches that disappeared from the provider-live feed. It does not refresh locally `NS` matches that have already become provider-terminal.

### Secondary contributing cause

```text
B. SCHEDULER / POLLING GAP
```

Completed fixtures depend on daily date synchronization or daily repair if they were never observed locally as live. There is no bounded, frequent completed-status reconciliation for recent local `NS` matches.

### Not root cause

* `D. STATUS NORMALIZATION BUG`: not supported; `status.short` is preserved.
* `E. TERMINAL TRANSITION DETECTION BUG`: not supported for `NS -> FT`; predicate accepts it.
* `F. TERMINAL CLEANUP ORDER BUG`: not supported; cleanup follows persistence/transition handling.
* `G. TRANSACTION / COMMIT BUG`: possible in generic exception paths, but no concrete evidence for the observed mismatch.
* `H. CACHE CONSISTENCY ISSUE`: not supported as the primary DB mismatch cause.
* `I. PROVIDER RESPONSE / API ISSUE`: provider timing is normal, but the Fover polling selection does not guarantee capture.

## 20. Required Answers

1. **Why can Provider=FT and Local=NS occur?**  
   The local fixture can remain `NS` because the live sync selection excludes local `NS` rows; the provider-terminal fixture is not necessarily fetched again until daily sync/repair.

2. **Is it temporary synchronization lag?**  
   Sometimes, but not merely ordinary latency. It is a design gap that can persist until a daily fixture refresh covers the match.

3. **How long can it remain?**  
   Up to the next applicable daily sync/repair cycle, subject to date/time coverage and provider response availability. The 60-second live job does not bound this for local `NS` rows.

4. **Will the next FixtureSync cycle definitely update NS->FT?**  
   No. The next live cycle does not select local `NS` rows. Daily sync/repair may update it if the fixture is included.

5. **Does NS->FT count as a terminal transition?**  
   Yes, if the fixture reaches `_process_sync_with_candidates()`.

6. **Does terminal transition create REQUIRED?**  
   Yes for `FT`, `AET`, or `P` from a status in `NON_TERMINAL_STATUSES`, subject to allowed league, identity, parsing, and transaction success.

7. **Can the system miss the terminal transition permanently?**  
   It can miss it indefinitely through the live path; daily sync/repair provides eventual opportunities but not a hard guarantee.

8. **Can active cleanup remove a match before finalization?**  
   Not in the processed-fixture path. Cleanup follows transition detection. The primary issue is failure to select the fixture before cleanup.

9. **Is an implementation defect confirmed?**  
   Yes: an active-match selection/scheduler design defect exists for locally `NS` fixtures that become provider-terminal without first being observed as live.

10. **What exact change is required?**  
    A separate implementation phase should add a bounded recent-fixture terminal reconciliation path. It must select recent supported local non-terminal fixtures, re-fetch provider status by fixture ID or an equivalent authoritative recent-fixture feed, pass results through the existing FixtureSyncService, and preserve the current transaction/lock/finalization architecture. It must not modify status directly or bypass the service pipeline.

## 21. Architecture Compliance

The Provider -> Service -> SyncService -> Repository -> Database layering is preserved for processed fixtures. The scheduler is a trigger, but its live selection is incomplete for local `NS` terminal reconciliation.

## 22. Phase 6 Impact

* Phase 6.1 architecture: no contradiction.
* Phase 6.2 failure/state matrix: no contradiction; missing finalization discovery is upstream of the matrix.
* Phase 6.3 implementation: identity/partial implementation is not the root cause.
* Phase 6.4 tests: missing explicit status-transition selection coverage.
* Phase 6.5 Real E2E: **HOLD** until the recent-terminal reconciliation gap is separately addressed and tested, or a genuinely captured lifecycle transition becomes available.

## 23. Exact Recommended Fix

**File/component:** `app/services/scheduler.py` and the existing `FixtureSyncService` entry path.  
**Method area:** live/recent fixture selection and scheduled fixture synchronization.  

Current behavior:

* live job fetches `/fixtures?live=all`
* stale cleanup only selects local statuses in `LIVE_STATUSES`
* lineup refresh selects future local `NS` rows, not recently completed rows

Expected behavior:

* add a bounded recent-fixture reconciliation selection for local non-terminal matches, including `NS`
* fetch provider fixture status through `FixtureProvider`
* pass the provider fixture through `_process_sync_with_candidates()`
* allow the existing `previous_status in NON_TERMINAL_STATUSES` and terminal-status predicate to create finalization `REQUIRED`
* preserve outer commit, resource locking, cache ordering, and Event independence

Risks:

* additional provider request volume
* duplicate processing across daily/live/reconciliation jobs, mitigated by existing identity/upsert and locks
* rate-limit pressure, requiring bounded selection and scheduling

Required tests:

* `NS -> FT` creates finalization candidate
* `LIVE -> FT` through stale reconciliation
* all supported terminal statuses
* local non-terminal selection horizon
* rollback does not create finalization
* concurrent reconciliation is idempotent

No fix was implemented in this audit.

## 24. Final Decision

```text
FIXTURE SYNC ROOT-CAUSE AUDIT = FAIL — IMPLEMENTATION DEFECT CONFIRMED

Provider → Local Status Mapping = direct status.short preservation
Terminal Transition Detection = correct for NS/LIVE/1H/2H/etc. → FT
NS → FT = accepted if processed, but not guaranteed to be selected
LIVE → FT = supported through stale-local-live cleanup
Scheduler = 60-second live feed plus daily/repair jobs
Active Match Selection = excludes local NS from live stale reconciliation
Terminal Cleanup = after transition handling when processed
Transaction = outer owner, atomic when processed
Cache = post-commit; not primary root cause
Historical Evidence = no concrete current mismatch row captured; design evidence confirms gap
Test Coverage = missing explicit NS→FT selection/transition tests

PRIMARY ROOT CAUSE = C. ACTIVE-MATCH SELECTION GAP
SECONDARY CAUSE = B. SCHEDULER / POLLING GAP

IMPLEMENTATION FIX REQUIRED = YES

PHASE 6.5 STATUS = HOLD

NEXT STEP = Separate controlled implementation and regression phase for recent terminal fixture reconciliation
```
