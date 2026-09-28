# LIVE & LINEUP RUNTIME HEALTH REPORT

## Overall Status

FAIL

Audit date: 2026-09-16. The API and dependencies are reachable, and the provider is available, but the dedicated scheduler/worker is not running in the observed runtime. A live provider fixture is therefore missing locally, and the database contains a lineup-to-analytics projection gap.

## Runtime

Application: RUNNING. `/health/ready` returned HTTP 200 with `{"status":"ready","postgres":true,"redis":true}`. One Uvicorn process tree was observed; no duplicate Uvicorn tree was found.

Scheduler: NOT RUNNING. No `worker.py` process was present, port 8001 was closed, and no scheduler job execution logs were available. The API process does not start the scheduler; the dedicated worker is the scheduler entry point.

PostgreSQL: HEALTHY. The application readiness check passed and read-only queries succeeded.

Redis: HEALTHY. The application readiness check passed.

Provider: AVAILABLE. The configured `FixtureProvider.get_live_fixtures()` call succeeded, returned one fixture, and completed in approximately 682 ms. The provider wrapper does not expose the underlying HTTP status; no credentials or headers were logged.

## LIVE SYNC

Scheduler: Configured job `sync_live_matches`, interval 60 seconds, `max_instances=1`. It is registered by `LiveUpdateScheduler.start()`, which is called only by `worker.py` through `scheduler_service.py`.

Provider: One live fixture was returned: provider fixture `1639452`, status `2H`, elapsed `61`, score `1-2`.

Code Path: `LiveUpdateScheduler._sync_live_matches_job()` -> `football_service.sync_live_matches()` -> `FixtureSyncService.sync_live_matches()` -> `FixtureSyncService._process_sync()` / `process_fixture()` -> `MatchRepository` / database. The scheduler owns commit/rollback, applies active-match updates after commit, and invalidates the live-match cache after successful commit.

Runtime Execution: FAIL. No worker or scheduler was running, so no current `LIVE_SYNC_*` scheduler execution evidence exists. The naturally live provider fixture had no matching local row, so it was not updated locally.

Database Update: FAIL for the observed live fixture. Provider fixture `1639452` was `2H`, 61 minutes, `1-2`; local match lookup returned no row. No status was manually changed and no fixture was fabricated.

Lock: Implementation uses the scheduler live advisory lock plus the shared `fixture_query:global` resource lock. `max_instances=1` prevents APScheduler overlap. Focused lock tests passed.

Transaction: Implementation keeps the outer scheduler transaction owner, commits only after successful fixture processing, rolls back provider/service failure, and releases locks in `finally` blocks. Focused transaction tests passed.

Cache: Successful live sync deletes the `live_matches` cache after commit. Cache failure is logged without changing the database transaction outcome. This was verified by source inspection and focused tests; no live scheduler cache event was observed.

Failure Recovery: Source and focused tests cover rollback and continuation after job failure. No live failure injection was performed, per the audit scope.

Status: FAIL

## LINEUP SYNC

Triggers: `refresh_lineups` scheduler job every 15 minutes; terminal finalization through `FixtureSyncService.finalize_pending_lineups()`; manual/API calls through `FootballAPIService` and `LineupService`. The job is configured but was not running because the worker was absent.

Provider: AVAILABLE for naturally persisted fixtures. Five sampled persisted fixtures each returned two provider team lineups. No lineup data was fabricated and no lineup rows were written during this audit.

Player Identity: The implementation routes provider lineup data through `PlayerIdentityResolutionService`; no direct `LineupSyncService` -> `PlayerRepository` identity bypass was found. Identity and lineup focused tests passed.

Lineup Persistence: Database contains 71 `match_lineups` rows. The sampled rows had canonical lineup payloads. However, 14 finalization records are currently `RETRYABLE` with `LINEUP_PARTIAL` / `missing_player_identity`, at attempt counts up to 7.

Analytics Projection: FAIL. Only 16 of 71 lineup matches have analytics rows. Finalization correlation showed 55 `SUCCESS` records but only 15 with analytics projections; all 14 `RETRYABLE` records have lineups and no analytics projection. One `TERMINAL` record also has a lineup without analytics. This is a current database consistency defect, not merely an unavailable natural test case.

Lock: Scheduler and finalization paths use the shared `lineup:{match_id}` resource lock. Focused resource-lock and lineup-lock tests passed.

Transaction: Lineup service flushes but does not own the outer transaction; scheduler/finalization owns commit/rollback. Projection failure is handled as rollback-required. Focused transaction and projection tests passed.

Cache: Scheduler and finalization invalidate the lineup cache after successful commit. No live cache event was available because the worker was not running.

Idempotency: Database checks found duplicate lineup keys = 0 and duplicate analytics business keys = 0. A repeated live lineup execution was not performed because the scheduler was absent and the audit did not justify an additional production write.

Failure Recovery: Focused failure, identity, projection, and transaction tests passed. Current retry state demonstrates retry metadata exists, but the absent scheduler prevents verifying the next live retry cycle.

Status: FAIL

## Finalization -> Lineup

Status: NOT NATURALLY OBSERVED during this audit. No natural terminal transition was created or forced. The implementation path is present: fixture processing creates finalization work, and finalization invokes `sync_match_lineup(..., allow_terminal_status=True)` under the lineup resource lock. Current database state contains 55 `SUCCESS`, 14 `RETRYABLE`, and 57 `TERMINAL` finalization records; the retryable records require follow-up after the worker is restored.

## Database Integrity

Snapshot:

| Table | Rows |
|---|---:|
| matches | 1,820 |
| live matches | 0 local rows at audit query time |
| players | 7,735 |
| teams | 258 |
| player_team_memberships | 7,818 |
| match_lineups | 71 |
| analytics_match_lineups | 697 |
| match_events | 8,766 |
| match_lineup_finalization | 126 |

Required checks:

* NULL player identities: 0
* NULL team identities: 0
* Duplicate player identities: 0
* Duplicate team identities: 0
* Orphan memberships: 0
* Orphan lineups: 0
* Orphan analytics: 0
* Orphan events: 0
* Orphan finalizations: 0
* Duplicate lineup keys: 0
* Duplicate analytics keys: 0
* Duplicate finalization keys: 0
* Invalid match statuses: 0
* Lineup-to-analytics projection coverage: 16/71 matches

All referential and duplicate checks: PASS. End-to-end lineup projection consistency: FAIL.

## Logs

Current Errors: No persisted application or scheduler log file was available in the workspace. The worker metrics port was unavailable, so current scheduler counters could not be queried.

Historical Errors: Workspace documentation records prior runtime limitations, but those entries were not treated as current log evidence.

New Errors: Current runtime evidence is the absent worker/scheduler, the missing local row for provider fixture `1639452`, and the current retryable/projection gap described above.

## Tests

Focused: PASS, 143 passed, 6 warnings. Suites covered live sync, stale-match selection, finalization, lineup service, player identity, player integration, analytics, locks, transaction ownership, cache transaction behavior, and scheduler recovery.

Full: FAIL, 658 passed, 1 failed, 19 warnings. The failure is `tests/test_team_upsert_on_conflict.py::test_ensure_teams_exist_resolves_existing_masters_and_reports_missing`; the implementation returns an additional `resolved` field. This is unrelated to the live/lineup runtime findings but must be tracked separately.

## Defects Found

1. Dedicated scheduler/worker is not running in the observed production runtime. This disables live sync, lineup refresh, and scheduled finalization retries.
2. Provider live fixture `1639452` has no matching local match row while the provider reports `2H`, elapsed 61, score `1-2`.
3. Lineup analytics projection coverage is incomplete: 55 successful finalizations do not have analytics projections, and 14 retryable partial finalizations have no analytics projections.

## Implementation Changes

NONE. No architecture, production data, statuses, lineups, analytics, locks, or transactions were modified during this audit.

## Final Classification

LIVE SYNC: FAIL

LINEUP SYNC: FAIL

OVERALL: FAIL

The failures are runtime/data-consistency findings, not proof that the frozen source architecture is absent. Restore and verify the dedicated worker, then investigate the lineup finalization/projection records before declaring the runtime healthy.