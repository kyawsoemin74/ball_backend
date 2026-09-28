# Fixture Sync Status Reconciliation Implementation Report

## 1. Current Architecture Before Change

Provider fixture payloads flowed through `FixtureProvider` and `FixtureSyncService` into the Match upsert. The scheduler triggered live, daily, and repair synchronization and owned the outer commit/rollback. Terminal transitions were detected during fixture processing and created an idempotent finalization `REQUIRED` record.

## 2. Confirmed Root Cause

The live stale cleanup query selected only local live-status matches. A local `NS` match that became provider-terminal without first being observed as live was not selected by the 60-second live job. Daily synchronization was eventual rather than a bounded reconciliation guarantee.

## 3. New Architecture

Recent reconciliation now selects bounded, allowed-league local non-terminal matches and re-checks them through `FixtureProvider`. Returned fixtures use the existing FixtureSyncService processing and finalization path. The scheduler only triggers the service and owns the outer transaction.

## 4. Recent Reconciliation Selection Strategy

`MatchRepository.get_recent_non_terminal()` selects:

* allowed leagues only
* local statuses in the existing `NON_TERMINAL_STATUSES` set
* `match_time` from 24 hours in the past through 24 hours in the future
* rows with provider fixture identities
* at most 200 rows per run, ordered by match time and local identity

Terminal local rows and unlimited historical rows are excluded.

## 5. Provider Fetch Strategy

Provider fixture IDs are rechecked through the existing `FixtureProvider.get_fixtures_by_ids()` capability in batches of 20. Provider exceptions and malformed responses return a failed result without entering fixture processing or marking a match terminal.

## 6. `process_fixture` Flow

`FixtureSyncService.process_fixture()` is the single-fixture entry point over the existing shared processing pipeline. The reconciliation path passes its provider response into the same `_process_sync_with_candidates()` implementation used by live and daily sync, preserving league filtering, identity resolution, upsert, flush, transition handling, and active-match updates.

## 7. Terminal Transition Detection

The existing definitions were preserved. A transition is detected when the persisted local status is in `NON_TERMINAL_STATUSES` and the normalized provider status is in `FINAL_LINEUP_TERMINAL_STATUSES` (`FT`, `AET`, or `PEN`). Tests cover `NS`, `LIVE`, `HT`, `1H`, `2H`, `ET`, and `P` to `FT`.

## 8. Finalization Handoff

The existing `FinalLineupFinalizationRepository.create_required()` remains the only terminal handoff. Its existing match-key lookup prevents duplicate finalization records. Fixture synchronization does not fetch lineups, resolve players, or project analytics.

## 9. Scheduler Changes

`LiveUpdateScheduler` now triggers `reconcile_recent_non_terminal` every five minutes under its own advisory lock and the existing fixture-query resource lock. The scheduler performs commit/rollback, post-commit live-cache invalidation, and finalization triggering as the existing sync jobs do; it contains no fixture selection or transition business logic.

## 10. Transaction Behavior

Fixture and finalization writes remain flush-only within the service/repository path. The scheduler remains the outer transaction owner for scheduled reconciliation. Provider failure rolls back/no-ops before fixture processing. Per-fixture savepoints and existing rollback behavior remain unchanged.

## 11. Cache Behavior

Live-match cache invalidation occurs after a successful outer commit. Provider status and previous status are read from the provider/database processing path, not from cache.

## 12. Failure Handling

Timeout/connection/provider response failures return `success=False`, do not invoke `_process_sync`, and do not create or update finalization records. Existing retry/finalization failure handling remains unchanged.

## 13. Automated Test Results

Focused validation passed:

* `69 passed, 6 warnings`
* Covered bounded candidate selection, provider-ID recheck, provider failure safety, all required non-terminal to `FT` transitions, scheduler behavior, transaction ownership, and existing live sync behavior.
* Production and test modules compiled successfully.
* Editor diagnostics reported no errors in touched files.

## 14. Real Provider Reconciliation Result

PASS. A dynamic read-only discovery selected a current provider-terminal/local-non-terminal candidate with no existing finalization record. The normal `reconcile_recent_non_terminal()` path then rechecked 15 bounded candidates, fetched 15 fixtures, updated 15 matches, and produced finalization candidates. The dynamically selected `2H -> FT` match received finalization status `REQUIRED` after the owning database session committed.

No fixture ID was hard-coded and no status, finalization, lineup, or analytics row was manually inserted or edited.

## 15. Database Integrity Result

Read-only checks after reconciliation:

* duplicate provider fixture identities: `0`
* invalid fixture identities: `0`
* duplicate finalization rows: `0`
* orphan finalization rows: `0`

Lineup, analytics, and event orphan checks were unavailable because the local database schema does not expose the queried table names/relations. They are reported as unavailable, not as passing.

## 16. Historical-Data Protection Result

The reconciliation query is bounded to the configured recent window and excludes terminal local statuses. No historical repair or direct historical data mutation was performed. The real run processed only rows selected by the bounded query.

## 17. Files Changed

* `app/repositories/match_repository.py`
* `app/services/fixture_sync_service.py`
* `app/services/football.py`
* `app/services/scheduler.py`
* `tests/test_live_sync_phase6.py`
* `tests/test_final_lineup_sync.py`
* `docs/FIXTURE_SYNC_STATUS_RECONCILIATION_IMPLEMENTATION_REPORT.md`

## 18. Files Not Changed

The frozen lineup finalization state machine, lineup services, player resolver, analytics pipeline, provider status definitions, database schema, and historical records were not redesigned or repaired.

## 19. Known Limitations

The local database could not execute the lineup, analytics, and event orphan queries because their expected table names were unavailable. The real provider run proved fixture transition and `REQUIRED` creation, but did not run the downstream lineup finalization lifecycle because that is a separate provider-dependent operation.

## 20. Final Decision

```text
IMPLEMENTATION STATUS: PASS
TEST STATUS: PASS - 69 focused tests passed
REAL RECONCILIATION STATUS: PASS - dynamic provider-backed 2H -> FT path created REQUIRED
DATABASE STATUS: PASS for available checks; three cross-domain checks unavailable
HISTORICAL DATA STATUS: PROTECTED - bounded selection, no historical repair
FINAL DECISION: PASS with documented database-query limitation
NEXT STEP: Run the existing downstream finalization/integrity suite against the deployment schema with correctly mapped table names
```