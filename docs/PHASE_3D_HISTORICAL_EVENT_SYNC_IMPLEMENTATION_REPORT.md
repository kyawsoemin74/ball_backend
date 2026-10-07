# Phase 3D — Historical Event Sync Implementation Report

**Project:** Fover Backend  
**Date:** 2026-10-07  
**Verdict:** PARTIAL

## 1. Implementation Summary

Implemented terminal Event synchronization through the existing durable Match
finalization lifecycle and converted the admin Event sync route into an
explicit first-snapshot backfill workflow.

- `FT`, `AET`, and `PEN` finalization attempts now synchronize Events under
  the existing per-Match Event lock before marking the finalization successful.
- Event sync failures return retryable finalization failures; they cannot mark
  finalization `SUCCESS`.
- The Event Scheduler now refreshes only active-registry Matches whose
  statuses are in the existing live-refresh allowlist.
- The admin endpoint requires `operation=HISTORICAL_BACKFILL`, a past
  timezone-aware Match time, a terminal status, and no existing Event rows.
- Historical repair/replacement is rejected by this endpoint and remains
  deferred.

## 2. Files Changed

Phase 3D implementation:

- `.gitignore` — unignore the focused Phase 3D test module.
- `app/api/matches.py` — explicit first-snapshot backfill guards and response.
- `app/services/final_match_sync_service.py` — Event sync in the finalization
  lifecycle before success.
- `app/services/fixture_sync_service.py` — enqueue durable finalization only
  for a transition from a live status to `FT`/`AET`/`PEN`; remove the dormant
  alternate Event-finalization helper.
- `app/services/football.py` — inject the existing EventService into the
  finalizer.
- `app/services/scheduler.py` — restrict Event refresh to live statuses and
  invalidate Event cache after committed finalization.
- `tests/test_phase3d_event_sync.py` — focused terminal, scheduler, backfill,
  cache, and authorization-contract tests.

The Event Team identity files already contained Phase 3 changes before this
implementation. This phase did not change
`app/services/event_sync_service.py` or `app/repositories/event_repository.py`.

## 3. Existing Architecture Reused

- Reused `MatchFinalizationRepository` and its existing `PENDING`/`RUNNING`/
  `FAILED`/`SUCCESS` state machine, attempt counter, and retry delays.
- Reused the existing finalization candidate polling/processing path in
  `LiveUpdateScheduler`; no new scheduler, table, or finalization mechanism
  was added.
- Reused `EventService.sync_match_events`, `run_with_resource_lock`, the
  existing transaction owner, and existing Event cache key.
- Reused `EventRepository.replace_match_events`; snapshot replacement
  semantics were not altered.
- Reused `current_active_admin`; no authentication mechanism was added.

## 4. Terminal Final Event Sync Flow

1. `FixtureSyncService` observes a transition from a live status to `FT`,
   `AET`, or `PEN` and ensures a durable pending Match finalization record.
   A correction from `NS`/`TBD` directly to a terminal status is not
   automatically enqueued as a historical finalization.
2. The existing finalization worker claims the record and calls
   `FinalMatchSyncService`.
3. The finalizer fetches and validates the authoritative terminal fixture,
   stages the final Match state, then takes the per-Match `events` lock and
   calls `EventService`/`EventSyncService`.
4. Only a successful Event sync permits `MatchFinalizationRepository` to stage
   `SUCCESS`.
5. The existing caller commits the transaction. Only after the successful
   commit does the finalization caller invalidate `live_matches` and that
   Match's Event cache.

For Event errors, unsuccessful results, and Event lock conflicts, the finalizer
returns `FAILED` with `retryable=True`. The existing finalization caller
persists the retry state. It does not claim finalization success.

## 5. Live Event Scheduler Flow

`_refresh_events_job` still discovers candidates through the existing active
Match registry, preserves the refresh interval, Event lock, caller-owned
commit/rollback, and post-commit cache invalidation. It now skips every status
outside the live allowlist, including terminal statuses and `NS`. It does not
enumerate historical Match rows and does not own terminal finalization.

## 6. Historical Admin Backfill Flow

The existing admin endpoint is retained and requires the explicit query
operation:

```text
POST /matches/sync/{match_id}/events?operation=HISTORICAL_BACKFILL
```

Under the existing Event lock it verifies that:

- the Match exists;
- status is one of `FT`, `AET`, or `PEN`;
- `match_time` is timezone-aware and in the past;
- no Event rows currently exist.

It then invokes the existing Event sync service, commits on success, and
invalidates Event cache after commit. The response identifies the operation
as `HISTORICAL_BACKFILL`. The existing admin dependency remains attached.

## 7. Historical Automatic Sync Prevention

- Event Scheduler candidates remain active-registry members only.
- Its status gate admits only the configured live-refresh statuses.
- Old terminal Matches are not selected for Event refresh.
- Fixture/finalization enqueueing is limited to transitions from a live
  status, avoiding automatic backfill when a stale `NS`/`TBD` Match is later
  corrected to terminal.
- Historical snapshot creation is only exposed through the explicit admin
  backfill operation.

## 8. Event Team Identity Preservation

The canonical identity pipeline remains unchanged:

```text
provider event.team.id
    → Team Master identity resolution
    → canonical Team.team_id
    → match_events.team_id
```

The finalization and historical backfill paths both delegate to the same
`EventSyncService`. Missing Team identity and Match-side mismatch continue to
fail before repository replacement using the existing error contracts. No
provider Team ID persistence, `provider_team_id`, migration, or read-time
mapping was introduced.

## 9. Player Master Side Effect

Event synchronization retains the existing Player identity resolver behavior.
An explicit historical first-snapshot backfill may create missing Player
Master identities through the existing `CREATE_NEW` path. No special Player
behavior was added, and no Player data was changed during this phase.

## 10. Transaction / Lock / Cache Verification

- Event sync service remains free of commit/rollback ownership.
- Terminal Event sync acquires the existing per-Match `events` resource lock.
- Historical backfill performs its no-existing-Events check and sync while
  holding the same per-Match Event lock.
- Event validation and full-snapshot replacement remain unchanged.
- Terminal Event cache invalidation is performed only after the finalization
  transaction commits.
- Focused tests verify backfill commit/cache behavior, terminal Event cache
  keys, and preservation of the admin dependency.

## 11. Test Results

Focused regression command:

```text
python -m pytest tests/test_phase3d_event_sync.py \
  tests/test_final_match_sync_phase3.py \
  tests/test_events_lock_alignment.py \
  tests/test_event_service_freshness.py \
  tests/test_event_team_identity.py -q
```

Result: **99 passed, 3 existing Pydantic deprecation warnings**.

The run covered FT/AET/PEN finalization, Event failure and exception behavior,
live/terminal scheduler gating, first-snapshot-only backfill, existing-row
repair rejection, admin dependency presence, and Event cache invalidation.

## 12. Runtime Verification Results

No live provider request, real database write, Redis mutation, or Match 476
operation was performed. Unit tests use isolated fakes/mocks.

- Event Team ID canonical mapping: **verified by focused tests**, no live sync.
- Event lock behavior: **verified by focused tests**, no live lock contention
  scenario.
- Admin authorization contract: **verified that the existing admin dependency
  remains attached**; no authenticated HTTP request was made.
- Redis cache invalidation: **verified by mocked cache calls**, not live Redis.
- FT/AET/PEN against natural fixtures: **NOT VERIFIED — NO SAFE NATURAL FIXTURE**.
- Real finalization state after provider Event failure: **not exercised against
  a live database**; failure and retry state behavior are covered by unit
  tests.

## 13. Database Impact

No database query or mutation was run for this implementation phase. No
migration or schema change was created.

## 14. Historical Data Safety

Match 476 was not used as a test target. Its existing 18 Event rows and
associated Player rows were not queried, synchronized, deleted, rewritten, or
repaired. The new backfill endpoint refuses to proceed when Event rows already
exist.

## 15. Known Limitations

- Historical Repair is intentionally unsupported; requests using any
  operation other than `HISTORICAL_BACKFILL` are rejected by request
  validation, and existing Event rows cause a conflict response.
- The admin backfill contract accepts only terminal `FT`/`AET`/`PEN` Matches
  with past scheduled times. It does not reconcile inconsistent provider and
  local status data before the admin explicitly requests the operation.
- Production provider behavior, Redis behavior, transaction failure modes,
  and natural finalization fixtures were not exercised.

## 16. Deferred Items

- Design and separately approve a historical Event Repair workflow, including
  its audit and rollback safeguards.
- Perform controlled runtime verification on safe non-production fixtures
  for natural FT/AET/PEN completion and real cache/lock behavior.
- No cleanup or repair of Match 476 or other historical Event/Player data is
  part of this phase.

## 17. Final Verdict

**PARTIAL**

The implementation and focused tests enforce explicit admin first-snapshot
backfill, prevent automatic historical Event refresh, and make terminal Event
sync part of the existing retryable Match finalization lifecycle. Required
natural-fixture/provider/database/Redis runtime scenarios were not performed
because no safe natural fixture was available and this phase did not authorize
historical provider sync. Therefore the runtime-evidence condition for PASS is
not met.
