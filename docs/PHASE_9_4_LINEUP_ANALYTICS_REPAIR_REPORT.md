# PHASE 9.4 - LINEUP -> ANALYTICS REPAIR REPORT

## 1. Executive Summary

The Lineup -> Analytics runtime contract was audited and hardened. Partial canonical lineup persistence can no longer report `success=True` when required canonical player identities are missing. A controlled historical repair service was added using the existing `AnalyticsProjectionService`, `AnalyticsLineupRepository`, lineup-scoped transaction lock, per-match transaction, and post-commit lineup-cache invalidation.

The database baseline contained 71 canonical lineups and only 16 projected matches. The repair safely reconstructed the 14 partial analytics scopes created during the initial broad repair attempt, then narrowed the repair to finalized `SUCCESS` lineups. The remaining 40 finalized-success lineups cannot be projected because their stored canonical `player_id` values no longer exist in `players`. The one remaining unfinalized invalid lineup has no canonical player IDs. No Player Master or Team Master records were created or changed.

Final status: **BLOCKED**. The pipeline and safety behavior pass, but the required historical missing-projection repair cannot be completed without the out-of-scope Player Master repair.

## 2. Scope

In scope: LineupSyncService, finalization integration, canonical lineup persistence, AnalyticsProjectionService, AnalyticsLineupRepository, repair orchestration, transaction ownership, lineup locks, cache invalidation, retry/failure semantics, tests, and read-only integrity checks.

Out of scope: Player Master, Team Master, Match Master, Event Sync, Final Live Sync, Analytics redesign, historical identity repair, and unrelated historical data repair.

## 3. Pre-Repair Architecture

The runtime path already used:

`LineupSyncService` -> `PlayerIdentityResolutionService` -> canonical `player_id` -> `match_lineups` -> `AnalyticsProjectionService.project_lineup()` -> `AnalyticsLineupRepository.replace_by_match()`.

Complete lineup updates and creates invoked projection. The partial branch persisted filtered canonical lineup data and returned `success=True` without invoking Analytics Projection. Finalization then treated partial results as retryable, but the result contract could expose a successful lineup state without analytics.

## 4. Baseline Database Counts

| Measure | Baseline |
|---|---:|
| Total `match_lineups` | 71 |
| Total `analytics_match_lineups` | 697 |
| Lineups with analytics | 16 |
| Lineups without analytics | 55 |
| Analytics orphans | 0 |
| Duplicate analytics business keys | 0 |
| Null analytics player IDs | 0 |
| Null analytics team IDs | 0 |
| Finalization `SUCCESS` | 55 |
| Finalization `RETRYABLE/LINEUP_PARTIAL` | 14 |
| Finalization `TERMINAL` | 57 |

The 55 missing scopes correlated to 40 finalized-success rows, 14 retryable partial rows, and one row without finalization. The 40 finalized-success rows had canonical-looking payload entries, but the canonical Player records were missing from `players`.

## 5. Root Cause

There were two distinct causes:

1. **Runtime gap:** the partial lineup branch wrote partial `match_lineups` data and returned success without calling `AnalyticsProjectionService.project_lineup()`.
2. **Historical data gap:** 40 finalized-success canonical lineup payloads reference local `player_id` values that do not exist in the current `players` table. The Analytics projector correctly rejects these with `PLAYER_IDENTITY_RESOLUTION_FAILED: canonical Player does not exist`.

The second condition cannot be repaired safely in Phase 9.4 because Analytics must not create or resolve Players.

## 6. Runtime Pipeline Audit

Complete canonical lineup create/update paths invoke `project_lineup()` before the outer transaction commits. Projection failures raise and are handled by the existing LineupSyncService failure path.

The partial path now returns `success=False`, `partial=True`, `failure_classification=PARTIAL`, and does not invoke Analytics. Finalization therefore cannot mark the attempt successful; existing retryable `LINEUP_PARTIAL` state remains observable.

Analytics continues to consume canonical local `player_id` values and does not call providers, resolve identities, or create Players.

## 7. Changes Implemented

* Added `LineupAnalyticsRepairService` for controlled missing-projection repair.
* Candidate selection requires an existing canonical `match_lineups` row with no analytics rows and a finalized `SUCCESS` record.
* Each repair uses `lineup:{match_id}` through the existing transaction-scoped resource lock.
* Each match has its own commit/rollback boundary.
* Successful repair invalidates the existing `fover:lineup:{match_id}` cache only after commit.
* Projection exceptions are logged and counted as per-match failures.
* Changed partial lineup persistence from `success=True` to `success=False` so analytics absence cannot be hidden as success.
* Added focused repair and partial-contract tests.

No Player, Team, Match, Event, Lineup identity, or Analytics architecture was redesigned.

## 8. Transaction Ownership

PASS. LineupSyncService, AnalyticsProjectionService, AnalyticsLineupRepository, and the repair service do not own the global transaction. The normal API/scheduler caller owns commit/rollback. The repair service intentionally uses a controlled per-match outer transaction because it is a separate repair orchestration entry point.

## 9. Locking

PASS. Repair uses the existing `lineup:{match_id}` resource lock. Resource locks are transaction-scoped through `pg_try_advisory_xact_lock`; no session-level pooled-connection lock was introduced. Final runtime inspection showed scheduler-level locks only; no lineup resource-lock leak was observed.

## 10. Cache Invalidation

PASS. Repair commits the analytics projection before invalidating `fover:lineup:{match_id}`. Projection failure rolls back and does not invalidate successful state. Existing runtime lineup cache behavior was preserved.

## 11. Retry / Failure Semantics

PASS for safe behavior. Partial identity failures remain `RETRYABLE/LINEUP_PARTIAL`. Historical projection failures are logged and counted without partial analytics commits. Missing canonical Players are not guessed, created, or resolved by Analytics. These 40 rows require a future Player Master/data-consistency phase before projection can succeed.

## 12. Historical Repair

The first controlled run selected 55 missing scopes. It projected 14 partial scopes before the data classification showed that partial rows must not receive analytics. Those 14 analytics scopes were removed using the existing analytics repository, lineup lock, commit, and cache path. The narrowed repair then selected only the 40 finalized-success missing scopes and safely failed all 40 because canonical Player rows were absent.

Final repair outcome:

```text
Finalized SUCCESS candidates: 40
Projected: 0
Skipped: 0
Failed safely: 40
Retryable partial candidates excluded: 14
Invalid/unfinalized candidate excluded: 1
```

No fabricated analytics rows remain from the broad initial attempt.

## 13. Idempotency Verification

The existing `replace_by_match()` strategy remains idempotent. Repeated repair attempts do not create duplicate business keys. Final counts show duplicate analytics business keys = 0 and analytics orphans = 0.

## 14. Focused Test Results

Focused Lineup, Analytics, Finalization, identity, lock, transaction, cache, and repair tests: `95 passed` before the final partial-contract expectation update; the final affected subset passed `55 passed`. The final full run below includes the updated expectations.

## 15. Full Test Results

Full backend suite: `669 passed, 1 failed`.

The remaining failure is unrelated and pre-existing: `tests/test_team_upsert_on_conflict.py::test_ensure_teams_exist_resolves_existing_masters_and_reports_missing` expects a response without the existing `resolved` field.

## 16. Real Runtime Verification

Automated pipeline verification: PASS. The worker remained healthy, `fover_scheduler_up=1`, and API readiness returned PostgreSQL and Redis ready. Scheduler lineup refresh execution was observed during the runtime window.

Real natural new Lineup Finalization -> Analytics E2E: NOT OBSERVED. No new natural finalization event was manufactured. Existing runtime data was repaired only through the controlled read-only-selected canonical projection service path.

## 17. Post-Repair Database Integrity

| Measure | Final |
|---|---:|
| `match_lineups` | 71 |
| `analytics_match_lineups` | 697 |
| Lineups with analytics | 16 |
| Lineups without analytics | 55 |
| Analytics orphans | 0 |
| Duplicate analytics business keys | 0 |
| Analytics player orphans | 0 |
| Analytics team orphans | 0 |
| Finalized-success missing projections | 40 |
| Retryable partial missing projections | 14 |
| Advisory locks at audit query | 2 scheduler-level locks; no lineup resource leak |
| Migration head | `20260915_missing_lineup_identity` |

Player, team, match, membership, event, and lineup identities were not changed by the repair.

## 18. Remaining Exceptions

* 40 finalized-success lineups cannot project because canonical Player rows are missing. Repairing those identities is explicitly out of scope.
* 14 retryable partial lineups lack complete canonical identity data and remain unprojected by design.
* One lineup has no finalization record and zero canonical player IDs; it was excluded.
* One unrelated pre-existing full-suite team-sync test fails.

## 19. Files Changed

* `app/services/lineup_sync_service.py`
* `app/services/lineup_analytics_repair_service.py`
* `tests/test_lineup_analytics_repair_service.py`
* `tests/test_player_identity_resolution_service.py`
* `docs/PHASE_9_4_LINEUP_ANALYTICS_REPAIR_REPORT.md`

Existing prior-phase changes in scheduler/resource-lock/fixture-sync files were not part of the Phase 9.4 repair.

## 20. Explicit Scope Confirmation

No Player Master, Team Master, Match Master, Event Sync, Final Live Sync, H2H, Odds, Standings, new scheduler, or unrelated historical repair was implemented. Phase 9.5, 9.6, and 9.7 were not started.

## 21. Final Status

**BLOCKED**

The runtime pipeline is repaired and fails closed, idempotency and integrity protections pass, and safe historical repair was attempted. The phase cannot be classified PASS because 40 valid-looking finalized lineups remain without analytics and cannot be safely projected while their canonical Player records are missing. Resolving those identities requires a separate permitted Player/data-consistency phase.