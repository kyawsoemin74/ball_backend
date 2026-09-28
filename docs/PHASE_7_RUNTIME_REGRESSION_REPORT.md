# PHASE 7 - RUNTIME REGRESSION REPORT

## Status

```text
PHASE 7 - PASS
SYSTEM RUNTIME HEALTHY
NO CURRENT MISMATCH
NO NEW RUNTIME DEFECT
```

## Runtime

PASS. The configured database is `localhost:5432/fover_db`; Alembic is at `20260915_missing_lineup_identity` (head); API-Football credentials are configured; `/health/ready` returned HTTP 200 with PostgreSQL and Redis ready. One API uvicorn process tree was present. No duplicate Fover scheduler/worker was found. The dedicated worker was not running in this shell, so scheduler execution was verified through configuration and automated job tests rather than a live worker interval.

## Baseline And Integrity

Read-only final state:

| Area | Count |
|---|---:|
| players | 7,735 |
| teams | 258 |
| matches | 1,754 |
| memberships | 7,818 |
| match_lineups | 71 |
| analytics_match_lineups | 697 |
| match_events | 8,766 |
| finalizations | 126 |
| missing_lineup_identities | 24 |
| NULL Player identities | 0 |
| NULL Team identities | 0 |
| duplicate Player identities | 0 |
| duplicate Team identities | 0 |
| duplicate Analytics keys | 0 |
| duplicate finalization keys | 0 |
| orphan memberships | 0 |
| orphan lineups | 0 |
| orphan Analytics rows | 0 |
| orphan events | 0 |
| orphan finalizations | 0 |

Analytics provider/local identity joins, Analytics Player/Team references, event Player/assist references, and invalid local Match statuses were also zero.

## Runtime Entry-Point Audit

Fixture paths remain:

```text
Manual season/date API or scheduler
  -> FootballAPIService
  -> FixtureSyncService
  -> Match/League/Team/Venue/Referee repositories
  -> API/Scheduler outer commit or rollback
  -> post-commit live cache invalidation
```

```text
Live feed scheduler
  -> FixtureSyncService.sync_live_matches()
  -> bounded stale provider-ID recheck
  -> shared fixture processing
  -> scheduler commit/rollback
```

```text
Recent reconciliation scheduler
  -> FixtureSyncService.reconcile_recent_non_terminal()
  -> MatchRepository bounded selection
  -> FixtureProvider batch recheck
  -> process_fixture()
  -> handle_terminal_transition()
  -> scheduler commit/rollback
```

```text
Terminal finalization
  -> FinalLineupFinalizationRepository REQUIRED/RETRYABLE discovery
  -> lineup:{match_id} resource lock
  -> FootballAPIService
  -> LineupSyncService
  -> PlayerIdentityResolutionService
  -> AnalyticsProjectionService
  -> finalization commit
  -> post-commit lineup cache invalidation
```

```text
Events
  -> EventService/EventSyncService
  -> events:{match_id} resource lock
  -> EventRepository
  -> event transaction owner
  -> post-commit event cache invalidation
```

No alternate provider-backed write path bypassing the frozen architecture was found. Event and Lineup remain independent.

## Fixture Sync

PASS. Provider status normalization remains direct and unchanged. Existing status sets cover `NS`, `TBD`, active statuses, and terminal statuses. The recent selector is bounded to 24 hours past/future and 200 rows, restricted to allowed leagues and local non-terminal statuses. Provider rechecks use batches of 20.

No current provider-terminal/local-non-terminal mismatch was found in the bounded candidate set. The result is:

```text
NO MISMATCH CASE AVAILABLE - SYSTEM CONSISTENT
```

## Recent Reconciliation

PASS. `reconcile_recent_non_terminal()` uses `process_fixture()` for each provider fixture. It does not contain fixture-specific patches, direct SQL status updates, or terminal business logic. Scheduler configuration triggers it every five minutes under advisory and resource locks; the scheduler remains trigger-only.

## Terminal Transition

NOT OBSERVED in real provider data during this audit. Automated coverage verifies:

```text
NS -> FT
LIVE -> FT
HT -> FT
1H -> FT
2H -> FT
ET -> FT
P -> FT
```

`handle_terminal_transition()` is the single finalization handoff and preserves idempotent `create_required()` behavior.

## Finalization

PASS by source, invariants, and tests. Frozen states remain `REQUIRED`, `RUNNING`, `RETRYABLE`, `TERMINAL`, and `SUCCESS`; no state-machine redesign occurred. Current records: `REQUIRED 0`, `RUNNING 0`, `RETRYABLE 14`, `SUCCESS 55`, `TERMINAL 57`. No duplicate or orphan finalizations exist.

## Player Identity

PASS. Player `(provider, provider_id)` is canonical. NULL/empty identities, duplicate identities, and accidental numeric `provider_id == player_id` coupling were zero. Lineup and Event paths retain resolver enforcement; CREATE_NEW uses PlayerSyncService.

## Team Identity

PASS. Team provider identities are complete and unique; NULL provider identities and duplicate identities were zero. Lineup and Analytics resolve Teams through provider identity.

## Lineup

PASS by source and automated regression. Provider lineup data is validated, Teams are resolved, Player identity resolution is mandatory, canonical local Player IDs are required before complete persistence, missing identities are tracked as partial/retryable, and duplicate lineup writes are protected by repository uniqueness and the lineup lock.

## Analytics Projection

PASS by source, invariants, and tests. Projection consumes canonical local `player_id` values, preserves provider IDs separately, validates Player/Team/Match ownership, fails closed for invalid identity/payload conditions, and has zero orphan or duplicate business-key rows.

## Event Independence

PASS. EventSyncService has its own provider, resolver, repository, `events:{match_id}` lock, transaction path, and cache invalidation. It does not depend on LineupSyncService or AnalyticsProjectionService, and Lineup does not depend on EventSync.

## Transaction Ownership

PASS. API routes and scheduler jobs own commit/rollback. Fixture, lineup, event, repository, resolver, and projection services do not own global transactions. Commit ambiguity handling remains in the existing odds path. Transaction ownership tests passed.

## Lock / Concurrency

PASS by automated regression. `lineup:{match_id}`, `events:{match_id}`, fixture-query, and resource locks release through `finally`; lock contention does not create duplicate work or consume lineup attempts.

## Cache

PASS. Manual API and scheduler/finalization callers invalidate after successful commit. Cache is not used for previous-status or transition decisions. Cache ordering and rollback tests passed.

## Implementation Fix

### Defect

Raw `print()` diagnostics remained in the stale live-fixture synchronization path, emitting provider response fields directly to stdout.

### Root Cause

Temporary stale-fixture debugging output was left in production orchestration after structured debug logging had already been added.

### Affected Module

`app/services/fixture_sync_service.py`

### Architecture Impact

No data or transaction behavior was changed, but raw stdout bypassed the structured runtime observability path and could pollute worker logs.

### Implementation Change

Removed the stray `print()` calls. Existing `logger.debug()` diagnostics remain.

### Regression Result

Focused live/reconciliation/finalization tests passed after the fix. No raw print markers remain in the fixture sync service.

## Automated Tests

Focused Phase 7 matrix:

```text
180 passed, 0 failed, 6 warnings
```

Full backend regression:

```text
658 passed, 1 failed, 19 warnings
```

The single full-suite failure is classified as an unrelated pre-existing TeamSync return-shape mismatch:

```text
tests/test_team_upsert_on_conflict.py::test_ensure_teams_exist_resolves_existing_masters_and_reports_missing
```

It expects the older result without the current `resolved` mapping. No Phase 7 test failed and no unrelated module was changed.

## New Runtime Defects

None after the raw stdout fix.

## Unrelated Pre-existing Failures

One TeamSync contract mismatch as described above.

## Final Format

```text
PHASE 7 — RUNTIME REGRESSION REPORT

Status: PASS
Runtime: PASS
Fixture Sync: PASS
Recent Reconciliation: PASS
Terminal Transition: NOT OBSERVED
Finalization: PASS
Player Identity: PASS
Team Identity: PASS
Lineup: PASS
Analytics Projection: PASS
Event Independence: PASS
Transaction Ownership: PASS
Lock / Concurrency: PASS
Cache: PASS
Database Integrity: PASS
Focused Tests: 180 passed, 0 failed
Full Regression: 658 passed, 1 unrelated failure
New Runtime Defects: None
Unrelated Pre-existing Failures: 1 TeamSync contract mismatch
```
